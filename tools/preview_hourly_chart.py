"""One verified H1 request and PNG preview, without Telegram or bot polling."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hourly_chart import get_hourly_candles, render_hourly_chart


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.chart-artifacts/hourly-preview.png')
    parser.add_argument('--data-output', help='Optional verified candle JSON evidence')
    args = parser.parse_args()
    candles, forming = await get_hourly_candles()
    image = await asyncio.to_thread(render_hourly_chart, candles, forming)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)
    if args.data_output:
        data_path = Path(args.data_output)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text(json.dumps(dict(candles=candles, has_forming=forming), indent=2), encoding='utf-8')
    print(f'Verified OANDA 1H: {len(candles)} candles; forming={forming}; last start={candles[-1]["start_timestamp"]}')
    print(f'PNG: {path.resolve()} ({len(image)} bytes)')


if __name__ == '__main__':
    asyncio.run(main())
