"""Read and acknowledge audit deliveries, or retry an exact saved delivery intent."""
import argparse
import asyncio
import json
from pathlib import Path

from carrier import Client


async def main(args):
    async with Client(url=args.url, credentials=args.credentials) as inbox:
        if args.intent:
            intent = json.loads(args.intent.read_text())
            message = await inbox.send(**intent)
            print(json.dumps({"message_id": message.id}))
            return
        page = await inbox.receive_pending()
        for message in page.messages:
            report = message.json()
            print(json.dumps({"message_id": message.id, "delivery": report}, indent=2))
            # Accept after parsing and displaying this example's report.
            await message.ack()
        print(f"Accepted {len(page.messages)} audit deliveries.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8877")
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--intent", type=Path, help="Retry using the original sender credentials")
    asyncio.run(main(parser.parse_args()))
