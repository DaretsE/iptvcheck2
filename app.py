"""
app.py — визуальная панель (Streamlit) поверх core.py.
Запуск локально:  streamlit run app.py
Деплой:           Streamlit Community Cloud (бесплатно) — см. README.md
"""
import time

import pandas as pd
import streamlit as st

import core

st.set_page_config(page_title="IPTV Analyzer Dashboard",
                   page_icon="📺", layout="wide")

# ----------------------------- стиль -----------------------------
st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; max-width: 1200px;}
      .stDataFrame {font-size: 0.92rem;}
      div[data-testid="stMetricValue"] {font-size: 1.6rem;}
      .small-note {color:#8b95a5; font-size:0.85rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📺 Анализатор рабочих IPTV-листов")
st.markdown(
    "Проверяет курируемый реестр публичных IPTV-плейлистов на доступность, "
    "считает каналы, подбирает EPG и строит рейтинг лучших рабочих листов."
)

# --------------------------- состояние ---------------------------
if "results" not in st.session_state:
    st.session_state.results = None
if "scanned_at" not in st.session_state:
    st.session_state.scanned_at = None

# --------------------------- панель управления ---------------------------
c1, c2, c3 = st.columns([2, 2, 3])
with c1:
    run = st.button("🔄 Найти и обновить базу", type="primary", use_container_width=True)
with c2:
    do_probe = st.toggle("Проверять потоки вживую", value=True,
                         help="Выборочно пингует несколько каналов из каждого листа. "
                              "Результат зависит от расположения сервера (гео-блокировки).")
with c3:
    sample = st.slider("Каналов на пробу из каждого листа", 3, 20, 8,
                       disabled=not do_probe)

with st.expander("➕ Добавить свои ссылки на плейлисты (по одной в строке)"):
    extra_raw = st.text_area("URL m3u/m3u8", height=100,
                             placeholder="https://example.com/playlist.m3u")

# --------------------------- запуск сканирования ---------------------------
if run:
    seeds = list(core.SEED_SOURCES)
    for line in (extra_raw or "").splitlines():
        u = line.strip()
        if u.startswith("http"):
            seeds.append({"name": f"Свой · {u.split('/')[-1][:30]}",
                          "url": u, "region": "—", "note": "добавлен вручную"})

    progress = st.progress(0.0, text="Подготовка…")
    log = st.empty()
    total = len(seeds)
    state = {"done": 0}

    def cb(done, tot, r):
        state["done"] = done
        mark = "✅" if r.reachable else "⛔"
        progress.progress(done / tot,
                          text=f"Проверено {done}/{tot} источников")
        log.markdown(f"{mark} **{r.name}** — {r.status} · каналов: {r.channels}")

    results = core.run_scan(seeds=seeds, do_probe=do_probe,
                            sample_size=sample, max_workers=6, progress_cb=cb)
    progress.progress(1.0, text="Готово")
    time.sleep(0.3)
    progress.empty()
    log.empty()

    st.session_state.results = results
    st.session_state.scanned_at = time.strftime("%Y-%m-%d %H:%M:%S")

# --------------------------- вывод результатов ---------------------------
results = st.session_state.results

if not results:
    st.info("База пуста. Нажмите **«Найти и обновить базу»**, чтобы запустить проверку.")
    st.markdown(
        f"<span class='small-note'>В реестре {len(core.SEED_SOURCES)} источников: "
        "iptv-org (мир/страны/категории), Free-TV/IPTV и др.</span>",
        unsafe_allow_html=True)
else:
    working = [r for r in results if r.reachable]
    total_channels = sum(r.channels for r in working)
    russian_count = sum(1 for r in results if r.is_russian)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Рабочих листов", f"{len(working)} / {len(results)}")
    m2.metric("Русскоязычных", russian_count)
    m3.metric("Всего каналов", f"{total_channels:,}".replace(",", " "))
    m4.metric("Обновлено", st.session_state.scanned_at or "—")

    if not do_probe or all(r.availability is None for r in results):
        st.caption("*Доступность потоков не проверялась (или проба отключена). "
                   "Рейтинг считается по доступности самого листа и числу каналов.")
    else:
        st.caption("*Доступность — доля ответивших каналов из случайной выборки. "
                   "Зависит от расположения сервера: из-за гео-блокировок часть "
                   "потоков может не отвечать, хотя у вас локально они работают.")

    COLS = {
        "Плейлист": st.column_config.LinkColumn("Плейлист", display_text="открыть m3u"),
        "EPG (телепрограмма)": st.column_config.LinkColumn("EPG", display_text="программа"),
        "Рейтинг": st.column_config.ProgressColumn("Рейтинг", min_value=0, max_value=100, format="%.0f"),
        "Каналов": st.column_config.NumberColumn("Каналов", format="%d"),
    }

    def render_table(rows: list, key: str):
        df = pd.DataFrame([r.as_row() for r in rows])
        df.index = range(1, len(df) + 1)
        st.dataframe(df, use_container_width=True, column_config=COLS,
                     height=min(560, 80 + 38 * len(df)), key=key)
        st.download_button(
            "💾 Скачать как CSV",
            data=df.to_csv(index_label="#").encode("utf-8-sig"),
            file_name=f"{key}.csv", mime="text/csv", key=f"dl_{key}",
        )

    # --- ГЛАВНАЯ ТАБЛИЦА: только русскоязычные листы ---
    russian = [r for r in results if r.is_russian]
    russian_ok = [r for r in russian if r.reachable]

    st.subheader("🇷🇺 Топ русскоязычных IPTV-листов")
    if russian:
        st.caption(f"Отобрано {len(russian)} русскоязычных листов из {len(results)} "
                   "проверенных (по флагу источника и доле кириллицы в названиях каналов).")
        render_table(russian, "russian_ranking")
        if russian_ok:
            top = russian_ok[0]
            st.success(f"**Рекомендуемый русскоязычный лист:** {top.name} — "
                       f"{top.channels} каналов · рейтинг {top.score:.0f}")
            st.code(top.url, language=None)
            if top.epg and top.epg != "—":
                st.markdown(f"EPG для него: {top.epg}")
    else:
        st.warning("Среди проверенных листов русскоязычных не найдено. "
                   "Попробуйте обновить базу позже или добавьте свои ссылки.")

    # --- ПОЛНЫЙ РЕЙТИНГ (все регионы) ---
    with st.expander(f"🌍 Полный рейтинг всех листов ({len(results)})"):
        render_table(results, "full_ranking")

with st.expander("ℹ️ Как это работает и важные оговорки"):
    st.markdown(
        """
- **Источники.** Используется курируемый реестр реально поддерживаемых публичных
  плейлистов (iptv-org, Free-TV/IPTV и др.), которые сами агрегируют каналы со
  всего мира. Это надёжнее, чем скрейпить Telegram/поисковики: «мёртвые» ссылки
  были главной причиной, почему наивный поиск возвращал пустоту.
- **Проверка.** Для каждого листа: загрузка файла → проверка `#EXTM3U` →
  подсчёт каналов → (опц.) выборочный пинг потоков → подбор EPG → рейтинг.
- **Гео-блокировки.** Если развернуть на Streamlit Community Cloud (США/ЕС),
  часть страновых потоков может не отвечать с сервера, хотя у вас они работают.
  Поэтому доступность листа и число каналов в рейтинге важнее, чем выборочный пинг.
- **EPG.** Берётся из заголовка плейлиста (`url-tvg`); если он мёртв —
  подставляется первый живой из запасного списка.
- Плеер для просмотра: VLC, Kodi (PVR IPTV Simple Client), TiviMate, IPTV Smarters.
        """
    )
