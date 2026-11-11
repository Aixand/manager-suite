"""Readable, durably reserved names for native agent sessions."""
import asyncio
import secrets
import uuid

from agent_manager.errors import Error
from agent_manager.storage.locks import locks
from agent_manager.validation import name


# Keep these local, lowercase and easy to spell. Words are deliberately unrelated
# to task content; the profile supplies the useful context.
ADJECTIVES = tuple("""
able active agile airy amber ample ancient
apt arctic arid artful awake aware azure balanced
basic beaming beloved better big blissful blue bold
bouncy brave breezy brief bright brisk broad bronze
bubbly busy calm candid capable careful caring casual
certain cheerful cherry classic clean
clear clever close cloudless coastal colorful comfy cool
coral cosmic cozy crafty crisp crystal curious
curly daily dainty daring decent deep deft
delicate devoted direct distant downy dreamy dry eager
early earthy easy elder elegant emerald
even fair faithful fancy fast fearless feathery festive
fine firm fit floral flowing fluffy flying
fond free fresh friendly frosty funny
gentle giant gifted giving glad glassy gleaming gleeful
glossy glowing golden graceful grand grassy grateful
gray great green growing happy hardy
healthy hearty helpful hidden high hollow honest hopeful
humble hushed icy ideal ivory jolly
joyful joyous keen kind large lasting leafy
lean level light lilac lively lofty lone lovely
low loyal lucky lunar lush magic mellow
merry mild minty misty modern modest mossy moving
narrow natural neat new nice nimble
noble novel oaken
open oval patient peachy pearly perky
petite pink plain playful pleasant plucky plush poetic
polished polite proud pure quick quiet radiant rapid
ready regal relaxed rich ripe rising robust
rosy round royal ruby rustic sandy scenic
serene sharp shiny shy silent silky silver simple
sincere sleek sleepy slender slim smart smooth snug
snowy soft solar solid sound spacious speedy splendid
spring steady stellar still stout sturdy sunny
swift tall tame tan tender thankful thin
tidy tiny trusty vivid warm wild wise
""".split())

NOUNS = tuple("""
acorn agate airfield airplane almond alpaca amber anchor
ant antelope anvil apple apricot arch
armadillo arrow ash aspen aster atlas attic aurora
avocado axle azalea baboon badger bagel bamboo banana
band barn barrel basin basket bass bat bay
beach beacon bean bear beaver beech beetle bell
berry birch bison blanket bloom blossom blueberry board
boat bobcat bolt bonnet book boot boulder bowl
box branch bread breeze bridge brook broom bubble
bucket bud buffalo bugle bulb bun burrow bush
butterfly button cabin cactus cake camel camera camp
canal candle canoe canyon cape capybara caramel cardinal
caribou carnation carrot cart cashew castle cat cedar
celery cello chain chair chalk cheetah cherry
chestnut chickadee chickpea chimney chisel chive chipmunk chocolate
cicada cinder cinnamon circle clam clay cliff
cloak cloud clover coach coast cobalt cobweb cocoa
coconut cod coffee comet compass condor cone cookie
copper coral cork corn cotton cougar cove
crab crane creek cricket crocus crow crown crystal
cuckoo cupcake currant cushion cypress daffodil dahlia daisy
dam dandelion dawn deer delta den desert diamond
dinghy dish dolphin dome donkey door dove dragonfly
drift drum duck dune eagle earth egret elderberry
elm ember emerald emu engine evergreen falcon fan
fawn feather fennel fern ferret field fig finch
fir firefly fish flag flamingo fleece flint flute
foam fog forest fork fountain fox frog
frost fruit garden garlic garnet gate gazelle
gecko gem gerbil ginger giraffe glacier glade glass
glider globe glowworm goat gold goldfinch goose gopher
gourd granite grape grass gravel grove guava gull
hamlet hammock hamster hare harp harvest hat haven
hawk hay hazel hazelnut hearth heather hedgehog heron
hibiscus hickory hill holly honey honeysuckle horn
horse hound house hummingbird hyacinth ibis iceberg icicle
iguana ink inlet iris island ivory ivy jackal
jacket jaguar jar jasmine jay jellyfish jewel juniper
kale kangaroo kayak kelp kettle key kingfisher kite
kiwi kitten koala koi lagoon lake lamb lamp
lantern larch lark laurel lavender lawn leaf
leek lemon lemur leopard lettuce lilac lily lime
linden linen lion lizard llama lobster log lotus
lychee lynx macaroon magpie magnolia mallard mango
maple marble marigold marmot marsh meadow melon mesa
meteor mica mill millet minnow mint mirror mitten
mole monkey moon moose morning moss moth mountain
mouse mulberry mushroom mussel mustard narwhal nectar nest
net nettle newt nightingale nut oak oasis oat
ocean ocelot olive onion opal orchid oriole osprey
ostrich otter owl oyster paddle pagoda palm panda
pansy papaya paper papyrus parakeet parsley parsnip partridge
pasture path peach peanut pear pearl pebble pecan
pelican pen penguin pepper perch petal petunia pheasant
piano pigeon pillow pine pineapple pistachio plank
planet plant plateau platypus plum pocket pond pony
pool poplar poppy porch potato prairie prawn prism
puffin pumpkin quail quartz rabbit raccoon radish raft
rain rainbow raisin raven ray reed reef reindeer
ridge river road robin rock rocket rook rope
rose rosemary sail salmon sand sandpiper sapphire
satchel scallop scarf scooter seal seed sequoia
shell shore shrimp shrub silo sky slate sled
sloth snail snow soap sock sparrow
spice spider spruce squash squirrel star starfish
stone stork stream string sugar summit sunflower swan
""".split())


def allocate_name(state, profile, run_id, occupied=()):
    """Reserve in state while its caller holds the manager's registry lock.

    Reservations outlive run finalization so an old reconnect command cannot
    silently select a later managed run with the same name.
    """
    name(profile)
    reserved = state.setdefault("session_names", {})
    used = set(occupied) | set(reserved) | {
        row.get("runtime_name", row.get("launch", {}).get("name"))
        for row in state.get("runs", {}).values()
    }
    for _ in range(128):
        adjective, noun = secrets.choice(ADJECTIVES), secrets.choice(NOUNS)
        # Undercurrent accepts 128 ASCII bytes. Keep the complete prefix for
        # ordinary profiles and shorten exceptionally long ones without a hash.
        prefix = profile[:128 - len(adjective) - len(noun) - 2]
        candidate = f"{prefix}-{adjective}-{noun}"
        if candidate not in used:
            reserved[candidate] = run_id
            return candidate
    raise Error("Could not allocate an unused session name; retry the launch")


def _submission(manager, run_id, occupied):
    with locks(manager.lock_root, "registry", wait=True):
        state = manager.state()
        record = state["runs"][run_id]
        if record["status"] != "prepared":
            raise Error("Managed launch changed before submission; reconcile it without replay")
        launch = record["launch"]
        from agent_manager.runtime.continuation.coordinator import validate_submission
        validate_submission(manager, record, run_id)
        if record.get("runtime_name") and launch["name"] in occupied:
            record["runtime_name"] = launch["name"] = allocate_name(
                state, record["agent"], run_id, occupied
            )
        record["status"] = "submitting"
        manager.save(state)
        return dict(launch)


def _rejected_name(manager, run_id, launch):
    with locks(manager.lock_root, "registry", wait=True):
        state = manager.state()
        record = state["runs"][run_id]
        if record["status"] != "submitting" or record["launch"] != launch:
            raise Error("Managed launch changed after rejection; reconcile it without replay")
        # A changed payload must not reuse the runtime's rejected request key.
        # The public managed run ID and caller's idempotency key remain stable.
        record["launch"]["idempotency_key"] = uuid.uuid4().hex
        record["status"] = "prepared"
        manager.save(state)


async def start(manager, run_id, runtime):
    """Submit a saved name, rerolling only on a proven absent name conflict."""
    from agent_manager.runtime.runs import read_run
    record = await asyncio.to_thread(read_run, manager, run_id)
    if record["status"] != "prepared":
        raise Error("Managed launch is not prepared; reconcile it without replay")
    generated = bool(record.get("runtime_name"))
    occupied = {session.name for session in await runtime.list()} if generated else set()
    for attempt in range(8):
        launch = await asyncio.to_thread(_submission, manager, run_id, occupied)
        try:
            return await runtime.start(**launch)
        except Exception as error:
            if not (generated and attempt < 7
                    and getattr(error, "product_category", None) == "name_conflict"
                    and getattr(error, "effect_certainty", None) == "known_absent"):
                raise
            occupied.add(launch["name"])
            await asyncio.to_thread(_rejected_name, manager, run_id, launch)
