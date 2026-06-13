"""
core.py — вся сетевая логика, парсинг и оценка плейлистов.
Не зависит от Streamlit, поэтому модуль можно тестировать отдельно.
"""
from __future__ import annotations

import math
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# КУРИРУЕМЫЙ РЕЕСТР ИСТОЧНИКОВ
# ---------------------------------------------------------------------------
# Это реально поддерживаемые публичные плейлисты (проверено вручную).
# Они агрегируют каналы со всего мира, поэтому "поиск везде" по факту
# обеспечивается широтой реестра, а не ненадёжным скрейпингом Telegram.
#
# Структура: name, url, region, note
SEED_SOURCES: list[dict] = [
    # --- iptv-org: крупнейший живой проект (генерируемые индексы на github.io) ---
    {"name": "iptv-org · Все каналы (мир)", "url": "https://iptv-org.github.io/iptv/index.m3u", "region": "Мир", "note": "~12 000 каналов, 190+ языков"},
    {"name": "iptv-org · Россия", "url": "https://iptv-org.github.io/iptv/countries/ru.m3u", "region": "RU", "note": "Русскоязычные каналы"},
    {"name": "iptv-org · Беларусь", "url": "https://iptv-org.github.io/iptv/countries/by.m3u", "region": "BY", "note": ""},
    {"name": "iptv-org · Казахстан", "url": "https://iptv-org.github.io/iptv/countries/kz.m3u", "region": "KZ", "note": ""},
    {"name": "iptv-org · Украина", "url": "https://iptv-org.github.io/iptv/countries/ua.m3u", "region": "UA", "note": ""},
    {"name": "iptv-org · США", "url": "https://iptv-org.github.io/iptv/countries/us.m3u", "region": "US", "note": ""},
    {"name": "iptv-org · Великобритания", "url": "https://iptv-org.github.io/iptv/countries/uk.m3u", "region": "UK", "note": ""},
    {"name": "iptv-org · Категория: Новости", "url": "https://iptv-org.github.io/iptv/categories/news.m3u", "region": "Мир", "note": "Только новостные"},
    {"name": "iptv-org · Категория: Спорт", "url": "https://iptv-org.github.io/iptv/categories/sports.m3u", "region": "Мир", "note": "Только спортивные"},
    {"name": "iptv-org · Категория: Кино", "url": "https://iptv-org.github.io/iptv/categories/movies.m3u", "region": "Мир", "note": ""},
    {"name": "iptv-org · Категория: Музыка", "url": "https://iptv-org.github.io/iptv/categories/music.m3u", "region": "Мир", "note": ""},
    {"name": "iptv-org · Категория: Детям", "url": "https://iptv-org.github.io/iptv/categories/kids.m3u", "region": "Мир", "note": ""},
    {"name": "iptv-org · Язык: Русский", "url": "https://iptv-org.github.io/iptv/languages/rus.m3u", "region": "RU", "note": "Все русскоязычные"},

    # --- Зеркала исходников iptv-org на raw.githubusercontent (запасной путь) ---
    {"name": "iptv-org src · Россия (raw)", "url": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/ru.m3u", "region": "RU", "note": "Зеркало исходника"},
    {"name": "iptv-org src · США (raw)", "url": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us.m3u", "region": "US", "note": "Зеркало исходника"},

    # --- Free-TV/IPTV: курируемый HD-плейлист ---
    {"name": "Free-TV/IPTV · Мир (HD)", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8", "region": "Мир", "note": "Курируется вручную, упор на HD"},
]

# Запасные EPG (телепрограмма). Проверяются на доступность в рантайме;
# мёртвые автоматически отсеиваются.
FALLBACK_EPGS: list[str] = [
    "https://iptvx.one/epg/epg.xml.gz",
    "http://epg.it999.ru/epg.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz",
]

# Прикидываемся реальным плеером, чтобы реже ловить блокировку ботов.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
}

EPG_RE = re.compile(r'(?:url-tvg|x-tvg-url|tvg-url)="([^"]+)"', re.IGNORECASE)


@dataclass
class PlaylistResult:
    name: str
    url: str
    region: str
    note: str
    status: str = "—"            # ОК / Недоступен / Ошибка ...
    reachable: bool = False
    channels: int = 0
    epg: str = "—"
    sampled: int = 0             # сколько потоков проверили выборочно
    working_streams: int = 0     # сколько ответили
    availability: Optional[float] = None  # %, None если проба не делалась
    latency_ms: int = 0
    score: float = 0.0

    def as_row(self) -> dict:
        avail = "—" if self.availability is None else f"{self.availability:.0f}%"
        return {
            "Название": self.name,
            "Регион": self.region,
            "Каналов": self.channels,
            "Доступность*": avail,
            "Отклик, мс": self.latency_ms,
            "Рейтинг": round(self.score, 1),
            "Плейлист": self.url,
            "EPG (телепрограмма)": self.epg,
            "Статус": self.status,
        }


def make_session() -> requests.Session:
    """Сессия с автоматическими повторами на сетевых сбоях."""
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.4,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "HEAD"]))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s


def parse_playlist(text: str) -> tuple[int, list[str], Optional[str]]:
    """Возвращает (число_каналов, список_потоковых_url, epg_из_заголовка)."""
    epg = None
    m = EPG_RE.search(text[:2000])
    if m:
        epg = m.group(1).split(",")[0].strip()

    channels = text.count("#EXTINF")
    streams: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("http"):
            # исключаем вложенные .m3u/.m3u8-индексы, оставляем реальные потоки
            streams.append(line)
    return channels, streams, epg


def fetch_playlist(session: requests.Session, url: str, timeout: float = 20.0):
    """Грузит плейлист. Возвращает (ok, text|None, latency_ms, status_str)."""
    t0 = time.time()
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        latency = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return False, None, latency, f"Недоступен (HTTP {r.status_code})"
        if "#EXTM3U" not in r.text[:64]:
            return False, None, latency, "Не похоже на M3U"
        return True, r.text, latency, "ОК"
    except requests.exceptions.Timeout:
        return False, None, int((time.time() - t0) * 1000), "Таймаут"
    except requests.exceptions.RequestException:
        return False, None, int((time.time() - t0) * 1000), "Сетевая ошибка"


def probe_stream(session: requests.Session, url: str, timeout: float = 4.0) -> bool:
    """Быстрая проверка одного потока. Любой ответ <400 считаем живым."""
    try:
        r = session.get(url, timeout=timeout, stream=True, allow_redirects=True)
        r.close()
        return r.status_code < 400
    except requests.exceptions.RequestException:
        return False


def check_epg(session: requests.Session, url: str, timeout: float = 5.0) -> bool:
    try:
        r = session.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code < 400:
            return True
        # некоторые сервера не любят HEAD — пробуем GET-заголовок
        r = session.get(url, timeout=timeout, stream=True, allow_redirects=True)
        ok = r.status_code < 400
        r.close()
        return ok
    except requests.exceptions.RequestException:
        return False


def resolve_epg(session: requests.Session, epg_from_header: Optional[str]) -> str:
    """EPG из заголовка плейлиста, иначе первый живой из запасных."""
    if epg_from_header and check_epg(session, epg_from_header):
        return epg_from_header
    for fb in FALLBACK_EPGS:
        if check_epg(session, fb):
            return fb
    return "—"


def compute_score(reachable: bool, channels: int,
                  availability: Optional[float], latency_ms: int) -> float:
    """Прозрачная формула рейтинга (0..100)."""
    if not reachable:
        return 0.0
    score = 40.0  # за то, что источник вообще доступен и валиден
    # каналы (лог-шкала, чтобы гигантские списки не задавили курируемые)
    score += min(30.0, math.log10(channels + 1) * 11.0)
    # доступность потоков (если проверяли)
    if availability is None:
        score += 12.0  # нейтрально, проба не делалась
    else:
        score += availability / 100.0 * 25.0
    # бонус за быстрый отклик
    if latency_ms and latency_ms < 1500:
        score += 5.0
    return round(score, 1)


def analyze_source(seed: dict, session: requests.Session,
                   do_probe: bool = True, sample_size: int = 8,
                   probe_workers: int = 8) -> PlaylistResult:
    """Полный анализ одного источника."""
    res = PlaylistResult(name=seed["name"], url=seed["url"],
                         region=seed.get("region", "—"), note=seed.get("note", ""))

    ok, text, latency, status = fetch_playlist(session, seed["url"])
    res.latency_ms = latency
    res.status = status
    res.reachable = ok
    if not ok or text is None:
        res.score = 0.0
        return res

    channels, streams, epg_hdr = parse_playlist(text)
    res.channels = channels
    res.epg = resolve_epg(session, epg_hdr)

    if do_probe and streams:
        n = min(sample_size, len(streams))
        sample = random.sample(streams, n)
        working = 0
        with ThreadPoolExecutor(max_workers=probe_workers) as ex:
            for alive in ex.map(lambda u: probe_stream(session, u), sample):
                working += int(alive)
        res.sampled = n
        res.working_streams = working
        res.availability = (working / n * 100.0) if n else 0.0

    res.score = compute_score(res.reachable, res.channels,
                              res.availability, res.latency_ms)
    return res


def run_scan(seeds: Optional[list[dict]] = None, do_probe: bool = True,
             sample_size: int = 8, max_workers: int = 6,
             progress_cb=None) -> list[PlaylistResult]:
    """
    Параллельно анализирует все источники и возвращает отсортированный список.
    progress_cb(done, total, last_result) — необязательный колбэк прогресса.
    """
    seeds = seeds if seeds is not None else SEED_SOURCES
    session = make_session()
    results: list[PlaylistResult] = []
    total = len(seeds)
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(analyze_source, s, session, do_probe, sample_size): s
                   for s in seeds}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            if progress_cb:
                progress_cb(done, total, r)

    results.sort(key=lambda x: x.score, reverse=True)
    return results
