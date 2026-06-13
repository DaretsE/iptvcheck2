"""
generate.py — запуск без интерфейса (для GitHub Actions или cron).
Строит общий рейтинг и отдельный топ русскоязычных листов.
Сохраняет ranking.json и RANKING.md.
По умолчанию проба потоков выключена: раннеры GitHub в США/ЕС, потоки гео-блокируются.
"""
import json
import time
from dataclasses import asdict

import core


def _table(rows):
    lines = [
        "| # | Название | Регион | Каналов | Рус.% | Рейтинг | Плейлист | EPG |",
        "|---|----------|--------|--------:|------:|--------:|----------|-----|",
    ]
    for i, r in enumerate(rows, 1):
        epg = r.epg if r.epg and r.epg != "—" else "—"
        epg_cell = f"[epg]({epg})" if epg != "—" else "—"
        lines.append(
            f"| {i} | {r.name} | {r.region} | {r.channels} | "
            f"{r.ru_ratio*100:.0f} | {r.score:.0f} | [m3u]({r.url}) | {epg_cell} |"
        )
    return "\n".join(lines)


def main(do_probe: bool = False):
    print("Сканирую источники…")
    results = core.run_scan(do_probe=do_probe, sample_size=6, max_workers=6)
    russian = [r for r in results if r.is_russian]

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "count": len(results),
        "russian_count": len(russian),
        "results": [asdict(r) for r in results],
        "russian": [asdict(r) for r in russian],
    }
    with open("ranking.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    md = [
        "# 🏆 Рейтинг рабочих IPTV-листов",
        f"_Обновлено: {payload['generated_at']}_",
        "",
        "## 🇷🇺 Топ русскоязычных листов",
        _table(russian) if russian else "_Русскоязычных листов не найдено._",
        "",
        f"## 🌍 Полный рейтинг ({len(results)} источников)",
        _table(results),
        "",
    ]
    with open("RANKING.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    ok = sum(1 for r in results if r.reachable)
    print(f"Готово: {ok}/{len(results)} доступны, из них русскоязычных листов: "
          f"{len(russian)}. Файлы: ranking.json, RANKING.md")


if __name__ == "__main__":
    import sys
    main(do_probe="--probe" in sys.argv)
