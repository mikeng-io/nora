
import asyncio, edge_tts, os
async def main():
    vs = await edge_tts.list_voices()
    hk = [(v["ShortName"], v["Gender"]) for v in vs if v["Locale"].startswith("zh-HK")]
    print("zh-HK voices:", hk)
    yue_cn = [(v["ShortName"], v["Gender"]) for v in vs if v["Locale"] in ("yue-CN","zh-CN")]
    print("yue-CN/zh-CN count:", len(yue_cn))
asyncio.run(main())
