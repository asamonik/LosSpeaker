import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AudioLines,
  Disc3,
  Radio,
  Library,
  SlidersHorizontal,
  Settings,
  Wifi,
  Bluetooth,
  Cast,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  ChevronRight,
  Search,
  Heart,
  Folder,
  Plus,
  Maximize2,
  X,
  Check,
  Music2,
  ArrowLeft,
  RefreshCw,
  ListMusic,
} from "lucide-react";
import "./style.css";
import Visualizer from "./Visualizer";
import ArtworkImage from "./ArtworkImage";
import type { VisualFrame } from "./visualizer-renderer";

type Track = {
  file?: string;
  directory?: string;
  title?: string;
  artist?: string;
  album?: string;
};
type Player = {
  active_source: string | null;
  status: string;
  title: string;
  artist: string;
  album: string;
  artwork: string | null;
  position: number;
  duration: number;
  volume: number;
  mode: string;
  output: string | null;
  source_format: string | null;
  output_format: string | null;
  sample_rate: number;
  channels: number;
  error: string | null;
  controls: string[];
};
type State = {
  player: Player;
  sources: Record<string, { available: boolean; detail: string }>;
  demo: boolean;
};
type Station = {
  stationuuid: string;
  name: string;
  url_resolved: string;
  country?: string;
  tags?: string;
  codec?: string;
};
type EQ = {
  samplerate: number;
  preamp: number;
  graphic: number[];
  parametric: { frequency: number; gain: number; q: number; kind: string }[];
  channels: number;
  requires_dsp: boolean;
};
const labels: Record<string, string> = {
  airplay: "AirPlay",
  spotify: "Spotify Connect",
  bluetooth: "Bluetooth",
  library: "Music library",
  radio: "Internet radio",
};
const sourceIcons: Record<string, typeof Radio> = {
  airplay: Cast,
  spotify: Disc3,
  bluetooth: Bluetooth,
  library: Library,
  radio: Radio,
};
const tabs = [
  ["Now playing", AudioLines],
  ["Sources", Wifi],
  ["Library", Library],
  ["Radio", Radio],
  ["Audio", SlidersHorizontal],
  ["Settings", Settings],
] as const;
async function api<T = any>(
  url: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch("/api/v1" + url, {
    method,
    headers:
      body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!r.ok) {
    let message = r.statusText;
    try {
      const d = await r.json();
      message =
        typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail);
    } catch {}
    throw Error(message);
  }
  return r.json();
}
function useSocket(path: string, onData: (d: any) => void, enabled = true) {
  const callback = useRef(onData);
  callback.current = onData;
  useEffect(() => {
    if (!enabled) return;
    let done = false;
    let ws: WebSocket;
    let retry: number;
    function connect() {
      ws = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`,
      );
      ws.onmessage = (e) => callback.current(JSON.parse(e.data));
      ws.onclose = () => {
        if (!done) retry = window.setTimeout(connect, 2500);
      };
    }
    connect();
    return () => {
      done = true;
      clearTimeout(retry);
      ws.close();
    };
  }, [path, enabled]);
}
function time(n: number) {
  return `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`;
}
function App() {
  const [state, setState] = useState<State | null>(null),
    [tab, setTab] = useState("Now playing"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [ambient, setAmbient] = useState(location.hash === "#visualizer"),
    [visualOpen, setVisualOpen] = useState(false),
    [visual, setVisual] = useState<VisualFrame>({
      available: false,
      spectrum: [] as number[],
      peak: [] as number[],
      reason: "Waiting for audio",
      label: "",
      output_peak: [] as number[],
    });
  const visualReceived = useRef(0);
  const visualFrame = useRef(visual);
  const [tracks, setTracks] = useState<Track[]>([]),
    [path, setPath] = useState(""),
    [query, setQuery] = useState(""),
    [browse, setBrowse] = useState("Folders"),
    [tagValues, setTagValues] = useState<string[]>([]),
    [queue, setQueue] = useState<Track[]>([]),
    [playlists, setPlaylists] = useState<any[]>([]);
  const [stations, setStations] = useState<Station[]>([]),
    [favourites, setFavourites] = useState<Station[]>([]),
    [radioQuery, setRadioQuery] = useState(""),
    [country, setCountry] = useState(""),
    [language, setLanguage] = useState(""),
    [genre, setGenre] = useState(""),
    [radioCached, setRadioCached] = useState(false),
    [favsOnly, setFavsOnly] = useState(false),
    [busy, setBusy] = useState(false);
  const [eq, setEq] = useState<EQ | null>(null),
    [custom, setCustom] = useState(false),
    [pin, setPin] = useState(""),
    [unlocked, setUnlocked] = useState(false),
    [outputs, setOutputs] = useState<any[]>([]),
    [output, setOutput] = useState(""),
    [dop, setDop] = useState(false),
    [devices, setDevices] = useState<any[]>([]),
    [pairUntil, setPairUntil] = useState(0),
    [storage, setStorage] = useState<any[]>([]),
    [storageForm, setStorageForm] = useState({
      name: "",
      kind: "smb",
      remote: "",
      username: "",
      password: "",
    });
  useSocket("/ws/state", setState);
  useSocket(
    "/ws/visualizer",
    (frame) => {
      visualReceived.current = performance.now();
      visualFrame.current = frame;
      if (!ambient) setVisual(frame);
    },
    (tab === "Now playing" && visualOpen) || ambient,
  );
  const p = state?.player;
  function openVisualizer() {
    location.hash = "visualizer";
    setAmbient(true);
  }
  function closeVisualizer() {
    if (location.hash === "#visualizer")
      history.replaceState(null, "", location.pathname + location.search);
    setAmbient(false);
    setVisual(visualFrame.current);
  }
  useEffect(() => {
    const changed = () => setAmbient(location.hash === "#visualizer");
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  async function act(fn: () => Promise<any>, message = "") {
    setError("");
    setBusy(true);
    try {
      await fn();
      if (message) {
        setNotice(message);
        window.setTimeout(() => setNotice(""), 4500);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    api<State>("/state")
      .then((snapshot) => setState((current) => current || snapshot))
      .catch((e) => setError(e.message));
    api<Station[]>("/radio/favourites")
      .then(setFavourites)
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (tab === "Library") {
      act(async () => {
        if (browse === "Queue") setQueue(await api("/queue"));
        else if (browse === "Playlists") setPlaylists(await api("/playlists"));
        else if (["Albums", "Artists", "Genres"].includes(browse) && !query) {
          const tag = { Albums: "album", Artists: "artist", Genres: "genre" }[
            browse
          ];
          const d = await api("/library/tags/" + tag);
          setTagValues(
            Array.isArray(d) ? d : (Object.values(d).flat() as string[]),
          );
        } else
          setTracks(
            await api(
              "/library?" + new URLSearchParams({ path, search: query }),
            ),
          );
      });
    }
    if (tab === "Audio")
      api("/dsp")
        .then((d) => {
          setEq(d.settings);
          setCustom(d.custom);
        })
        .catch((e) => setError(e.message));
    if (tab === "Settings")
      api<any[]>("/outputs")
        .then((d) => {
          setOutputs(d);
          setOutput(p?.output || d[0]?.id || "");
        })
        .catch((e) => setError(e.message));
  }, [tab, path, query, browse]);
  async function refreshAdmin() {
    setDevices(await api("/bluetooth"));
    setStorage(await api("/storage"));
  }
  async function radioSearch() {
    await act(async () => {
      const d = await api(
        "/radio/search?" +
          new URLSearchParams({
            name: radioQuery,
            countrycode: country,
            language,
            tag: genre,
          }),
      );
      setStations(d.stations);
      setRadioCached(d.cached);
      setFavsOnly(false);
    });
  }
  async function favourite(s: Station) {
    await act(async () => {
      const next = favourites.some((x) => x.stationuuid === s.stationuuid)
        ? favourites.filter((x) => x.stationuuid !== s.stationuuid)
        : [...favourites, s];
      setFavourites(await api("/radio/favourites", "PUT", next));
    });
  }
  const source = p?.active_source
    ? labels[p.active_source]
    : "No source selected";
  const cover = (
    <div className={"cover " + (p?.status === "playing" ? "spinning" : "")}>
      <ArtworkImage src={p?.artwork} alt={`${p?.album || p?.title} album artwork`} />
      <Disc3
        className="cover-placeholder"
        size={40}
        strokeWidth={1}
        aria-hidden="true"
      />
    </div>
  );
  const spectrum = (
    <div
      className="spectrum"
      aria-label={visual.available ? "Live pre-DSP spectrum" : visual.reason}
    >
      {Array.from({ length: 64 }, (_, i) => (
        <i
          key={i}
          style={{
            height: visual.available
              ? `${Math.max(3, (visual.spectrum[i] || 0) * 100)}%`
              : "3%",
            opacity: visual.available ? 1 : 0.25,
          }}
        />
      ))}
    </div>
  );
  const controls = (
    <div className="transport">
      <button
        aria-label="Previous track"
        disabled={!p?.controls.includes("previous")}
        onClick={() =>
          act(() => api("/player/command", "POST", { action: "previous" }))
        }
      >
        <SkipBack size={23} />
      </button>
      <button
        className="play-button"
        aria-label={p?.status === "playing" ? "Pause" : "Play"}
        disabled={
          !p?.controls.includes(p?.status === "playing" ? "pause" : "play")
        }
        onClick={() =>
          act(() =>
            api("/player/command", "POST", {
              action: p?.status === "playing" ? "pause" : "play",
            }),
          )
        }
      >
        {p?.status === "playing" ? (
          <Pause fill="currentColor" />
        ) : (
          <Play fill="currentColor" />
        )}
      </button>
      <button
        aria-label="Next track"
        disabled={!p?.controls.includes("next")}
        onClick={() =>
          act(() => api("/player/command", "POST", { action: "next" }))
        }
      >
        <SkipForward size={23} />
      </button>
    </div>
  );
  if (!state)
    return (
      <div className="loading">
        <AudioLines size={40} />
        <h1>LosSpeaker</h1>
        <p>{error || "Connecting to your listening room…"}</p>
        <button onClick={() => location.reload()}>Reconnect</button>
      </div>
    );
  return (
    <div className={ambient ? "app ambient" : "app"}>
      <header className="app-header" inert={ambient ? true : undefined}>
        <div className="header-inner">
          <a
            className="brand"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              setTab("Now playing");
            }}
          >
            <AudioLines size={22} strokeWidth={1.6} />
            <span>LosSpeaker</span>
          </a>
          <nav aria-label="Main navigation">
            {tabs.map(([name]) => (
              <button
                key={name}
                className={tab === name ? "active" : ""}
                aria-current={tab === name ? "page" : undefined}
                onClick={() => setTab(name)}
              >
                {name}
              </button>
            ))}
          </nav>
          {state.demo && <span className="header-note">Demo</span>}
        </div>
      </header>
      <main inert={ambient ? true : undefined}>
        <div className="page-heading">
          <h1>{tab}</h1>
        </div>
        {state.demo && (
          <p className="demo-banner">
            Audio services are simulated. <span>Settings PIN: 123456.</span>
          </p>
        )}
        {error && (
          <div role="alert" className="alert">
            <span>{error}</span>
            <button aria-label="Dismiss error" onClick={() => setError("")}>
              <X size={17} />
            </button>
          </div>
        )}
        {notice && (
          <div role="status" className="notice">
            <Check size={16} />
            {notice}
          </div>
        )}
        {p?.error && <div className="alert">{p.error}</div>}
        {tab === "Now playing" && (
          <>
            <section className="now-grid">
              <div className="art-area">
                {cover}
                <button
                  className="art-expand"
                  onClick={openVisualizer}
                  aria-label="Full screen artwork"
                >
                  <Maximize2 size={15} />
                </button>
              </div>
              <div className="now-detail">
                <div className="source-badge">
                  {source}
                  {p?.mode === "direct" && <span>Direct</span>}
                </div>
                <div className="track-details">
                  <h2>{p?.active_source ? p.title : "Nothing playing"}</h2>
                  <p>
                    {p?.active_source
                      ? p.artist
                      : "Choose a source to start listening."}
                  </p>
                  {p?.active_source && p.album && <small>{p.album}</small>}
                </div>
                <div className="timeline">
                  <input
                    aria-label="Playback position"
                    type="range"
                    min="0"
                    max={p?.duration || 1}
                    value={p?.position || 0}
                    disabled={!p?.controls.includes("seek")}
                    onChange={(e) =>
                      act(() =>
                        api("/player/command", "POST", {
                          action: "seek",
                          position: Number(e.target.value),
                        }),
                      )
                    }
                  />
                  <div>
                    <span>{time(p?.position || 0)}</span>
                    <span>{time(p?.duration || 0)}</span>
                  </div>
                </div>
                {controls}
                <div className="volume">
                  <Volume2 size={17} />
                  <input
                    aria-label="Master volume"
                    type="range"
                    min="0"
                    max="100"
                    value={p?.volume || 0}
                    disabled={p?.mode === "direct"}
                    onChange={(e) =>
                      act(() =>
                        api("/player/volume", "PUT", {
                          value: Number(e.target.value),
                        }),
                      )
                    }
                  />
                  <span>
                    {p?.mode === "direct" ? "FIXED" : p?.volume + "%"}
                  </span>
                </div>
              </div>
            </section>
            <div className="player-actions">
              <button className="outline" onClick={openVisualizer}>
                <AudioLines size={16} /> Open visualizer
              </button>
              <button className="outline" onClick={() => setTab("Sources")}>
                Choose source
              </button>
              <button onClick={() => setTab("Library")}>
                Browse library <ChevronRight size={14} />
              </button>
            </div>
            <details
              className="visual-panel"
              onToggle={(e) => setVisualOpen(e.currentTarget.open)}
            >
              <summary>
                Visualizer{" "}
                <span>
                  {visualOpen
                    ? visual.available
                      ? "Live · pre-DSP"
                      : visual.reason
                    : "Spectrum and levels"}
                </span>
                <ChevronRight size={15} />
              </summary>
              <div className="visual-content">
                {spectrum}
                <div className="frequency">
                  <span>20 Hz</span>
                  <span>100</span>
                  <span>1k</span>
                  <span>10k</span>
                  <span>20 kHz</span>
                </div>
                <div className="meters">
                  {(visual.output_peak?.length
                    ? visual.output_peak.map((_, i) => "OUT " + (i + 1))
                    : ["IN L", "IN R"]
                  ).map((label, i) => (
                    <div key={label}>
                      <span>{label}</span>
                      <div>
                        <i
                          style={{
                            width: `${visual.available ? Math.max(0, ((((visual.output_peak?.length ? visual.output_peak[i] : visual.peak[i]) ?? -80) + 80) / 80) * 100) : 0}%`,
                          }}
                        />
                      </div>
                      <small>
                        {visual.available
                          ? Math.round(
                              (visual.output_peak?.length
                                ? visual.output_peak[i]
                                : visual.peak[i]) ?? -80,
                            ) + " dB"
                          : "—"}
                      </small>
                    </div>
                  ))}
                </div>
              </div>
            </details>
            <details className="audio-info">
              <summary>
                Audio details <ChevronRight size={15} />
              </summary>
              <dl>
                <div>
                  <dt>Source format</dt>
                  <dd>{p?.source_format || "Not reported"}</dd>
                </div>
                <div>
                  <dt>Processing</dt>
                  <dd>
                    {p?.mode === "direct"
                      ? "Bypassed"
                      : `${(p?.sample_rate || 48000) / 1000} kHz`}
                  </dd>
                </div>
                <div>
                  <dt>Output</dt>
                  <dd>
                    {p?.mode === "direct"
                      ? "Hardware native"
                      : `${p?.output_format || "PCM"} · ${p?.channels} channels`}
                  </dd>
                </div>
              </dl>
            </details>
          </>
        )}
        {tab === "Sources" && (
          <>
            <p className="intro">
              Start playback from any source to switch automatically.
            </p>
            <div className="source-grid">
              {Object.entries(labels).map(([s, label]) => {
                const Icon = sourceIcons[s];
                return (
                  <article className="panel source-card" key={s}>
                    <div className="section-top">
                      <span className={"source-icon " + s}>
                        <Icon size={28} />
                      </span>
                      <span
                        className={
                          "pill " + (state.sources[s]?.available ? "ready" : "")
                        }
                      >
                        {state.sources[s]?.available ? "Ready" : "Offline"}
                      </span>
                    </div>
                    <h2>{label}</h2>
                    <p>
                      {s === "airplay"
                        ? "Open the AirPlay menu on your Apple device and choose LosSpeaker."
                        : s === "spotify"
                          ? "In Spotify, open “Connect to a device” and choose LosSpeaker. Spotify Premium required."
                          : s === "bluetooth"
                            ? "Open pairing in Settings, then connect to LosSpeaker from your phone."
                            : s === "library"
                              ? "Albums, folders and favourites from your own collection."
                              : "Find stations worldwide and save favourites."}
                    </p>
                    <small>{state.sources[s]?.detail}</small>
                    <button
                      className="outline"
                      onClick={() =>
                        setTab(
                          s === "library"
                            ? "Library"
                            : s === "radio"
                              ? "Radio"
                              : "Settings",
                        )
                      }
                    >
                      {s === "library"
                        ? "Browse library"
                        : s === "radio"
                          ? "Explore stations"
                          : "Connection settings"}
                      <ChevronRight size={15} />
                    </button>
                  </article>
                );
              })}
            </div>
          </>
        )}
        {tab === "Library" && (
          <>
            <div className="toolbar">
              <div className="search">
                <Search size={17} />
                <input
                  aria-label="Search music"
                  placeholder="Search your music…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <button
                className="outline"
                onClick={() =>
                  act(async () => {
                    const name = prompt("Save current queue as playlist");
                    if (name) await api("/playlists", "POST", { name });
                  }, "Playlist saved")
                }
              >
                <Plus size={16} />
                Save queue
              </button>
            </div>
            <div className="pills">
              {[
                "Folders",
                "Albums",
                "Artists",
                "Genres",
                "Playlists",
                "Queue",
              ].map((n) => (
                <button
                  className={browse === n ? "selected" : ""}
                  onClick={() => {
                    setBrowse(n);
                    setQuery("");
                  }}
                  key={n}
                >
                  {n}
                </button>
              ))}
            </div>
            {browse === "Folders" && (
              <div className="breadcrumb">
                <button
                  onClick={() =>
                    setPath(path.split("/").slice(0, -1).join("/"))
                  }
                >
                  <ArrowLeft size={16} />
                </button>
                Music / {path}
              </div>
            )}
            <section className="panel rows">
              {browse === "Queue"
                ? queue.map((t, i) => (
                    <div className="row" key={i}>
                      <ListMusic size={20} />
                      <div>
                        <b>{t.title || t.file}</b>
                        <small>{t.artist}</small>
                      </div>
                      <button
                        onClick={() =>
                          act(async () => {
                            await api("/queue/" + i, "DELETE");
                            setQueue(await api("/queue"));
                          })
                        }
                        aria-label="Remove track"
                      >
                        <X size={18} />
                      </button>
                    </div>
                  ))
                : browse === "Playlists"
                  ? playlists.map((t) => (
                      <button
                        className="row"
                        key={t.playlist}
                        onClick={() =>
                          act(() =>
                            api(
                              "/playlists/" +
                                encodeURIComponent(t.playlist) +
                                "/play",
                              "POST",
                            ),
                          )
                        }
                      >
                        <ListMusic />
                        <div>
                          <b>{t.playlist}</b>
                        </div>
                        <Play size={18} />
                      </button>
                    ))
                  : ["Albums", "Artists", "Genres"].includes(browse) && !query
                    ? tagValues.map((t) => (
                        <button
                          className="row"
                          key={t}
                          onClick={() => setQuery(t)}
                        >
                          <Disc3 />
                          <div>
                            <b>{t}</b>
                          </div>
                          <ChevronRight />
                        </button>
                      ))
                    : tracks.map((t, i) => (
                        <button
                          className="row"
                          key={i}
                          onClick={() =>
                            t.directory
                              ? setPath(t.directory)
                              : act(() =>
                                  api("/queue", "POST", {
                                    uri: t.file,
                                    play: true,
                                  }),
                                )
                          }
                        >
                          {t.directory ? (
                            <Folder size={23} />
                          ) : (
                            <Music2 size={23} />
                          )}
                          <div>
                            <b>
                              {t.title ||
                                t.directory ||
                                t.file?.split("/").pop()}
                            </b>
                            <small>
                              {t.artist}
                              {t.album ? " · " + t.album : ""}
                            </small>
                          </div>
                          {t.directory ? (
                            <ChevronRight size={17} />
                          ) : (
                            <Play size={17} />
                          )}
                        </button>
                      ))}
              {!busy &&
                ((browse === "Queue" && !queue.length) ||
                  (browse === "Playlists" && !playlists.length) ||
                  (![
                    "Queue",
                    "Playlists",
                    "Albums",
                    "Artists",
                    "Genres",
                  ].includes(browse) &&
                    !tracks.length)) && (
                  <div className="empty">
                    <Library size={32} />
                    <h3>Your collection starts here</h3>
                    <p>
                      Add a local, USB or NAS source in Settings, then scan it.
                    </p>
                  </div>
                )}
            </section>
          </>
        )}
        {tab === "Radio" && (
          <>
            <p className="intro">Search stations or play a stream URL.</p>
            <form
              className="toolbar"
              onSubmit={(e) => {
                e.preventDefault();
                radioSearch();
              }}
            >
              <div className="search">
                <Search size={17} />
                <input
                  placeholder="Search stations worldwide…"
                  aria-label="Search radio stations"
                  value={radioQuery}
                  onChange={(e) => setRadioQuery(e.target.value)}
                />
              </div>
              <button className="primary" disabled={busy}>
                Find stations
              </button>
              <button
                type="button"
                className="outline"
                onClick={() => setFavsOnly(!favsOnly)}
              >
                <Heart size={16} />
                {favsOnly ? "All stations" : "Favourites"}
              </button>
            </form>
            <div className="radio-filters">
              <input
                placeholder="Country code, e.g. AT"
                aria-label="Country code"
                value={country}
                onChange={(e) => setCountry(e.target.value.toUpperCase())}
              />
              <input
                placeholder="Language"
                aria-label="Language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              />
              <input
                placeholder="Genre / tag"
                aria-label="Genre"
                value={genre}
                onChange={(e) => setGenre(e.target.value)}
              />
            </div>
            {radioCached && (
              <p className="muted">
                Directory unavailable. Showing cached results.
              </p>
            )}
            <div className="radio-grid">
              {(favsOnly ? favourites : stations).map((s) => (
                <article className="panel station" key={s.stationuuid}>
                  <div className="section-top">
                    <span className="source-icon radio">
                      <Radio />
                    </span>
                    <button
                      aria-label="Toggle favourite"
                      onClick={() => favourite(s)}
                    >
                      <Heart
                        size={20}
                        fill={
                          favourites.some(
                            (x) => x.stationuuid === s.stationuuid,
                          )
                            ? "currentColor"
                            : "none"
                        }
                      />
                    </button>
                  </div>
                  <h3>{s.name}</h3>
                  <p>
                    {s.country || "Independent radio"} · {s.codec || "Live"}
                  </p>
                  <small>{s.tags?.split(",").slice(0, 3).join(" / ")}</small>
                  <button
                    className="outline"
                    onClick={() =>
                      act(() =>
                        api("/radio/play", "POST", { uri: s.url_resolved }),
                      )
                    }
                  >
                    <Play size={15} />
                    Tune in
                  </button>
                </article>
              ))}
            </div>
            {!stations.length && !favsOnly && (
              <div className="empty">
                <Radio size={38} />
                <h3>Find a station</h3>
                <p>Search by station, country or the music you love.</p>
              </div>
            )}
            <form
              className="panel manual-stream"
              onSubmit={(e) => {
                e.preventDefault();
                const url = new FormData(e.currentTarget).get("url");
                act(() => api("/radio/play", "POST", { uri: url }));
              }}
            >
              <h3>Have a stream URL?</h3>
              <div className="toolbar">
                <input
                  type="url"
                  name="url"
                  placeholder="https://station.example/stream"
                  required
                  aria-label="Custom stream URL"
                />
                <button className="outline">Play stream</button>
              </div>
            </form>
          </>
        )}
        {tab === "Audio" && eq && (
          <>
            <div className="audio-status panel">
              <SlidersHorizontal />
              <div>
                <h3>
                  {custom ? "Imported CamillaDSP profile" : "Audio processing"}
                </h3>
                <p>
                  {custom
                    ? "Custom routing is preserved. Reset the profile to use the EQ editors."
                    : "All sources share the same processing. Changes require an unlocked admin session."}
                </p>
              </div>
              <span className="pill ready">
                {p?.mode === "direct"
                  ? "DIRECT"
                  : eq.samplerate / 1000 + " kHz"}
              </span>
            </div>
            {custom ? (
              <button
                className="outline"
                onClick={() =>
                  act(async () => {
                    const d = await api("/dsp?replace=true", "PUT", {
                      ...eq,
                      requires_dsp: false,
                      channels: 2,
                    });
                    setEq(d.settings);
                    setCustom(false);
                  }, "Profile reset")
                }
              >
                Reset to stereo EQ
              </button>
            ) : (
              <>
                <section className="panel eq-panel">
                  <div className="section-top">
                    <h3>Graphic equalizer</h3>
                    <button
                      onClick={() =>
                        setEq({ ...eq, graphic: Array(10).fill(0) })
                      }
                    >
                      Reset
                    </button>
                  </div>
                  <div className="eq-bands">
                    {eq.graphic.map((gain, i) => (
                      <label key={i}>
                        <span>
                          {gain > 0 ? "+" : ""}
                          {gain} dB
                        </span>
                        <input
                          aria-label={`${[31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000][i]} Hz EQ`}
                          type="range"
                          min="-24"
                          max="12"
                          step="0.5"
                          value={gain}
                          onChange={(e) =>
                            setEq({
                              ...eq,
                              graphic: eq.graphic.map((v, j) =>
                                i === j ? Number(e.target.value) : v,
                              ),
                            })
                          }
                        />
                        <b>
                          {
                            [
                              "31",
                              "63",
                              "125",
                              "250",
                              "500",
                              "1k",
                              "2k",
                              "4k",
                              "8k",
                              "16k",
                            ][i]
                          }
                        </b>
                      </label>
                    ))}
                  </div>
                </section>
                <section className="panel">
                  <div className="section-top">
                    <h3>Parametric equalizer</h3>
                    <button
                      className="outline"
                      onClick={() =>
                        setEq({
                          ...eq,
                          parametric: [
                            ...eq.parametric,
                            {
                              frequency: 1000,
                              gain: 0,
                              q: 1.4,
                              kind: "Peaking",
                            },
                          ],
                        })
                      }
                    >
                      <Plus size={15} />
                      Add band
                    </button>
                  </div>
                  {eq.parametric.map((b, i) => (
                    <div className="param-band" key={i}>
                      {(["frequency", "gain", "q"] as const).map((k) => (
                        <label key={k}>
                          {k}
                          <input
                            type="number"
                            value={b[k]}
                            step="0.1"
                            onChange={(e) =>
                              setEq({
                                ...eq,
                                parametric: eq.parametric.map((x, j) =>
                                  i === j
                                    ? { ...x, [k]: Number(e.target.value) }
                                    : x,
                                ),
                              })
                            }
                          />
                        </label>
                      ))}
                      <select
                        aria-label="Filter type"
                        value={b.kind}
                        onChange={(e) =>
                          setEq({
                            ...eq,
                            parametric: eq.parametric.map((x, j) =>
                              i === j ? { ...x, kind: e.target.value } : x,
                            ),
                          })
                        }
                      >
                        {["Peaking", "Lowshelf", "Highshelf"].map((k) => (
                          <option key={k}>{k}</option>
                        ))}
                      </select>
                      <button
                        onClick={() =>
                          setEq({
                            ...eq,
                            parametric: eq.parametric.filter((_, j) => i !== j),
                          })
                        }
                        aria-label="Remove band"
                      >
                        <X />
                      </button>
                    </div>
                  ))}
                  {!eq.parametric.length && (
                    <p className="muted">
                      Add a frequency band to shape your sound precisely.
                    </p>
                  )}
                </section>
                <section className="panel form-grid">
                  <label>
                    Processing sample rate
                    <select
                      value={eq.samplerate}
                      onChange={(e) =>
                        setEq({ ...eq, samplerate: Number(e.target.value) })
                      }
                    >
                      {[44100, 48000, 88200, 96000, 176400, 192000].map((n) => (
                        <option value={n} key={n}>
                          {n / 1000} kHz
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Headroom / preamp (dB)
                    <input
                      type="number"
                      min="-60"
                      max="0"
                      value={eq.preamp}
                      onChange={(e) =>
                        setEq({ ...eq, preamp: Number(e.target.value) })
                      }
                    />
                  </label>
                  <p className="muted">
                    The selected rate and channel count must be supported by
                    your DAC. Start at 48 kHz with −6 dB headroom.
                  </p>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      act(() => api("/dsp", "PUT", eq), "DSP profile applied")
                    }
                  >
                    Apply audio settings
                  </button>
                </section>
              </>
            )}
            <details className="panel disclosure">
              <summary>
                Room correction & crossovers
                <ChevronRight size={15} />
              </summary>
              <div className="disclosure-content">
                <p className="muted">
                  Upload mono PCM FIR WAV files first, then import your
                  CamillaDSP YAML profile. Imported profiles require DSP and
                  preserve their output routing.
                </p>
                <div className="form-grid">
                  {[
                    ["FIR coefficients (.wav)", "/dsp/fir", ".wav"],
                    ["CamillaDSP profile (.yml)", "", ".yml,.yaml"],
                  ].map(([label, url, accept]) => (
                    <label className="upload" key={label}>
                      {label}
                      <input
                        type="file"
                        accept={accept}
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f) {
                            const body = new FormData();
                            body.append("file", f);
                            act(async () => {
                              await api(url || "/dsp/import", "POST", body);
                              if (!url) setCustom(true);
                            }, "File imported");
                          }
                        }}
                      />
                    </label>
                  ))}
                </div>
              </div>
            </details>
            <details className="panel disclosure">
              <summary>
                Playback mode
                <ChevronRight size={15} />
              </summary>
              <div className="disclosure-content">
                <p className="muted">
                  Direct mode gives MPD exclusive DAC access. EQ and
                  visualization are unavailable; volume is fixed. Native DSD/DoP
                  depends on your DAC and must be configured explicitly.
                </p>
                <button
                  className="outline"
                  onClick={() =>
                    act(
                      () =>
                        api("/player/mode", "PUT", {
                          mode: p?.mode === "direct" ? "processed" : "direct",
                        }),
                      "Playback mode changed",
                    )
                  }
                >
                  {p?.mode === "direct"
                    ? "Restore processed playback"
                    : "Enter direct playback"}
                </button>
              </div>
            </details>
          </>
        )}
        {tab === "Settings" && (
          <>
            <section className="panel">
              <div className="section-top">
                <h3>Administration</h3>
                <span className="pill">
                  {unlocked ? "Unlocked" : "PIN required"}
                </span>
              </div>
              <p className="muted">
                Playback is open to your home network. Configuration is
                protected by your admin PIN.
              </p>
              <form
                className="toolbar"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(async () => {
                    await api("/admin/login", "POST", { pin });
                    setUnlocked(true);
                    setPin("");
                    await refreshAdmin();
                  }, "Settings unlocked");
                }}
              >
                <input
                  aria-label="Admin PIN"
                  type="password"
                  inputMode="numeric"
                  placeholder="Admin PIN"
                  autoComplete="current-password"
                  value={pin}
                  onChange={(e) => setPin(e.target.value)}
                  required
                />
                <button className="primary">Unlock</button>
                {unlocked && (
                  <button
                    className="outline"
                    type="button"
                    onClick={() =>
                      act(async () => {
                        await api("/admin/logout", "POST");
                        setUnlocked(false);
                      })
                    }
                  >
                    Lock
                  </button>
                )}
              </form>
            </section>
            <section className="panel">
              <h3>Audio output</h3>
              <p className="muted">
                Choose a hardware device before starting playback. Unsupported
                audio formats fail with an error.
              </p>
              <div className="toolbar">
                <select
                  aria-label="Audio output"
                  value={output}
                  onChange={(e) => setOutput(e.target.value)}
                >
                  {outputs.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name} ({o.id})
                    </option>
                  ))}
                </select>
                <button
                  className="primary"
                  disabled={!unlocked || !output}
                  onClick={() =>
                    act(
                      () => api("/outputs", "PUT", { device: output, dop }),
                      "Output configured",
                    )
                  }
                >
                  Use this output
                </button>
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={dop}
                  onChange={(e) => setDop(e.target.checked)}
                />
                Enable DoP in direct mode only (confirm DAC compatibility first)
              </label>
            </section>
            <section className="panel">
              <div className="section-top">
                <h3>Bluetooth devices</h3>
                <button
                  className="outline"
                  disabled={!unlocked}
                  onClick={() =>
                    act(async () => {
                      const d = await api("/bluetooth/pair", "POST");
                      setPairUntil(d.pairing_until);
                    }, "Bluetooth pairing open for two minutes")
                  }
                >
                  <Bluetooth size={16} />
                  Open pairing
                </button>
              </div>
              <p className="muted">
                {pairUntil > Date.now() / 1000
                  ? "Pairing window opened. Select LosSpeaker on your phone."
                  : "Discoverability is disabled outside the pairing window."}
              </p>
              {devices.map((d) => (
                <div className="row" key={d.path}>
                  <Bluetooth />
                  <div>
                    {d.Alias || d.Name}
                    <small>
                      {d.Connected ? "Connected" : "Remembered device"}
                    </small>
                  </div>
                  <button
                    onClick={() =>
                      act(async () => {
                        await api(
                          "/bluetooth/" + d.path.split("/").pop(),
                          "DELETE",
                        );
                        await refreshAdmin();
                      })
                    }
                  >
                    Forget
                  </button>
                </div>
              ))}
              <button disabled={!unlocked} onClick={() => act(refreshAdmin)}>
                <RefreshCw size={14} />
                Refresh devices
              </button>
            </section>
            <section className="panel">
              <h3>Music storage</h3>
              <p className="muted">
                Sources are mounted read-only. Local paths must be inside
                /srv/losspeaker/local; USB sources use a filesystem UUID. SMB
                credentials are stored in a root-only file.
              </p>
              {storage.map((s) => (
                <div className="row" key={s.name}>
                  <Folder />
                  <div>
                    <b>{s.name}</b>
                    <small>
                      {s.kind} · {s.remote}
                    </small>
                  </div>
                  <button
                    onClick={() =>
                      act(
                        () => api("/library/scan", "POST", { name: s.name }),
                        "Library scan started",
                      )
                    }
                  >
                    Scan
                  </button>
                </div>
              ))}
              <form
                className="form-grid"
                onSubmit={(e) => {
                  e.preventDefault();
                  act(async () => {
                    setStorage(await api("/storage", "POST", storageForm));
                    setStorageForm({ ...storageForm, password: "" });
                  }, "Music source added");
                }}
              >
                <label>
                  Source name
                  <input
                    required
                    pattern="[a-zA-Z0-9_-]+"
                    value={storageForm.name}
                    onChange={(e) =>
                      setStorageForm({ ...storageForm, name: e.target.value })
                    }
                  />
                </label>
                <label>
                  Type
                  <select
                    value={storageForm.kind}
                    onChange={(e) =>
                      setStorageForm({ ...storageForm, kind: e.target.value })
                    }
                  >
                    {["smb", "nfs", "usb", "local"].map((k) => (
                      <option key={k}>{k}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Share / UUID / local path
                  <input
                    required
                    value={storageForm.remote}
                    placeholder="//nas/Music"
                    onChange={(e) =>
                      setStorageForm({ ...storageForm, remote: e.target.value })
                    }
                  />
                </label>
                {storageForm.kind === "smb" && (
                  <>
                    <label>
                      Username
                      <input
                        value={storageForm.username}
                        onChange={(e) =>
                          setStorageForm({
                            ...storageForm,
                            username: e.target.value,
                          })
                        }
                      />
                    </label>
                    <label>
                      Password
                      <input
                        type="password"
                        autoComplete="off"
                        value={storageForm.password}
                        onChange={(e) =>
                          setStorageForm({
                            ...storageForm,
                            password: e.target.value,
                          })
                        }
                      />
                    </label>
                  </>
                )}
                <button className="primary" disabled={!unlocked}>
                  Add source
                </button>
              </form>
            </section>
            <details className="panel disclosure">
              <summary>
                System health
                <ChevronRight size={15} />
              </summary>
              <div className="disclosure-content">
                {Object.entries(state.sources).map(([s, h]) => (
                  <div className="row" key={s}>
                    <span className={h.available ? "green-dot" : "grey-dot"} />
                    <div>
                      <b>{labels[s]}</b>
                      <small>{h.detail}</small>
                    </div>
                    <span>{h.available ? "Ready" : "Unavailable"}</span>
                  </div>
                ))}
              </div>
            </details>
          </>
        )}
      </main>
      {ambient && (
        <Visualizer
          input={() => ({ frame: visualFrame.current, received: visualReceived.current, playing: p?.status === "playing", scene: "Bloom", palette: "Aurora" })}
          title={p?.active_source ? p.title : "Nothing playing"}
          artist={p?.active_source ? p.artist : ""}
          artwork={p?.artwork || null}
          source={source}
          controls={p?.controls || []}
          onCommand={async (action) => { await api("/player/command", "POST", { action }); }}
          onClose={closeVisualizer}
        />
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
