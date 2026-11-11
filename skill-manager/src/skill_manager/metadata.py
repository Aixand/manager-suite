"""Safe native frontmatter parsing; never evaluates skill instructions."""
import yaml

from .codec import canonical, skill_name
from .errors import SkillError, require


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        require(isinstance(key, str), "YAML keys must be strings")
        require(key not in result, f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse(content, directory_name):
    try:
        text = content.decode("utf-8")
        require(len(content) <= 1024 * 1024, "SKILL.md exceeds the 1 MiB metadata inspection limit")
        lines = text.splitlines()
        require(lines and lines[0] == "---", "SKILL.md must begin with YAML frontmatter (---)")
        end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
        require(end is not None, "SKILL.md frontmatter requires closing ---")
        frontmatter = "\n".join(lines[1:end])
        require(not any(isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
                        for t in yaml.scan(frontmatter)), "YAML aliases/anchors are unsupported")
        data = yaml.load(frontmatter, Loader=UniqueLoader)
        require(isinstance(data, dict), "Skill metadata must be a mapping")
        require(set(data) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"},
                "Unknown frontmatter fields; put custom fields inside metadata")
        skill_name(data.get("name"))
        require(data["name"] == directory_name, "Metadata name must match the package directory")
        desc = data.get("description")
        require(isinstance(desc, str) and desc.strip() and len(desc) <= 1024,
                "description must be a nonempty string of at most 1024 characters")
        for field in ("license", "compatibility", "allowed-tools"):
            if field in data:
                require(isinstance(data[field], str) and data[field].strip(), f"{field} must be a nonempty string")
        if "compatibility" in data:
            require(len(data["compatibility"]) <= 500, "compatibility exceeds 500 characters")
        if "metadata" in data:
            require(isinstance(data["metadata"], dict) and
                    all(isinstance(k, str) and isinstance(v, str) for k, v in data["metadata"].items()),
                    "metadata must map strings to strings")
        canonical(data)
        return data
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise SkillError("validation", f"Invalid SKILL.md frontmatter: {exc}") from exc
