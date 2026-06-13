"""
generate.py — запуск без интерфейса (для GitHub Actions или cron).
Строит рейтинг и сохраняет ranking.json и RANKING.md.
По умолчанию проба потоков выключена: раннеры GitHub в США/ЕС, потоки гео-блокируются.
"""
import json
import time
from dataclasses import asdict

import core


def main(do_probe: bool = False):
    print("Сканирую источники…")
    results = core.run_scan(do_probe=do_probe, sample_size=6, max_workers=6)

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "count": len(results),
        "results": [asdict(r) for r in results],
    }
    with open("ranking.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    lines = [
        "# 🏆 Рейтинг рабочих IPTV-листов",
        f"_Обновлено: {payload['generated_at']}_",
        "",
        "| # | Название | Регион | Каналов | Рейтинг | Плейлист | EPG |",
        "|---|----------|--------|--------:|--------:|----------|-----|",
    ]
    for i, r in enumerate(results, 1):
        epg = r.epg if r.epg and r.epg != "—" else "—"
        lines.append(
            f"| {i} | {r.name} | {r.region} | {r.channels} | {r.score:.0f} | "
            f"[m3u]({r.url}) | {('[epg](' + epg + ')') if epg != '—' else '—'} |"
        )
    with open("RANKING.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    ok = sum(1 for r in results if r.reachable)
    print(f"Готово: {ok}/{len(results)} листов доступны. Файлы: ranking.json, RANKING.md")


if __name__ == "__main__":
    import sys
    main(do_probe="--probe" in sys.argv)
