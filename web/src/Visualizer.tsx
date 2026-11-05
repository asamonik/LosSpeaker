import { useEffect, useRef, useState } from "react";
import { AudioLines, Maximize2, Minimize2, Pause, Play, SkipBack, SkipForward, X } from "lucide-react";
import { palettes, scenes, startVisualizer, type Palette, type Scene, type VisualInput } from "./visualizer-renderer";
import "./visualizer.css";
import ArtworkImage from "./ArtworkImage";

type Props = {
  input: () => VisualInput;
  title: string;
  artist: string;
  artwork: string | null;
  source: string;
  controls: string[];
  onCommand: (action: string) => Promise<void>;
  onClose: () => void;
};

function saved<T extends string>(key: string, choices: readonly T[], fallback: T): T {
  try { const value = localStorage.getItem(key) as T; return choices.includes(value) ? value : fallback; } catch { return fallback; }
}

export default function Visualizer(props: Props) {
  const [scene, setScene] = useState<Scene>(() => saved("losspeaker.visualizer.scene", scenes, "Bloom"));
  const [palette, setPalette] = useState<Palette>(() => saved("losspeaker.visualizer.palette", Object.keys(palettes) as Palette[], "Aurora"));
  const [visible, setVisible] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [signal, setSignal] = useState({ live: false, playing: false, status: "Waiting for audio" });
  const [generation, setGeneration] = useState(0);
  const overlay = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const hideTimer = useRef(0);
  const latest = useRef(props);
  latest.current = props;
  const style = useRef({ scene, palette });
  style.current = { scene, palette };

  function reveal() {
    setVisible(true);
    clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      if (!overlay.current?.querySelector("button:focus-visible")) setVisible(false);
    }, 4500);
  }

  useEffect(() => {
    try {
      localStorage.setItem("losspeaker.visualizer.scene", scene);
      localStorage.setItem("losspeaker.visualizer.palette", palette);
    } catch {}
  }, [scene, palette]);

  useEffect(() => {
    const el = canvas.current!;
    const stop = startVisualizer(el, () => ({ ...latest.current.input(), ...style.current }));
    const restore = () => setGeneration((value) => value + 1);
    el.addEventListener("visualizer-restore", restore);
    return () => { stop(); el.removeEventListener("visualizer-restore", restore); };
  }, [generation]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = overlay.current;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    overlay.current?.focus();
    reveal();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); latest.current.onClose(); }
      if (event.key === "Tab") {
        reveal();
        const buttons = Array.from(overlay.current!.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === overlay.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === overlay.current)) { event.preventDefault(); first?.focus(); }
      }
    };
    const updateFullscreen = () => setFullscreen(document.fullscreenElement === overlay.current);
    document.addEventListener("keydown", keydown);
    document.addEventListener("fullscreenchange", updateFullscreen);
    const updateSignal = () => {
      const data = latest.current.input();
      const stale = performance.now() - data.received > 3000;
      const live = data.frame.available && data.playing && !stale;
      const status = !data.playing ? "Waiting for music" : stale ? "Reconnecting to audio" : data.frame.available ? "Live audio" : data.frame.reason || "Waiting for audio";
      setSignal(previous => previous.live === live && previous.playing === data.playing && previous.status === status ? previous : { live, playing: data.playing, status });
    };
    updateSignal();
    const freshness = window.setInterval(updateSignal, 100);
    return () => {
      clearTimeout(hideTimer.current); clearInterval(freshness);
      document.body.style.overflow = overflow;
      document.removeEventListener("keydown", keydown);
      document.removeEventListener("fullscreenchange", updateFullscreen);
      if (document.fullscreenElement === element) void document.exitFullscreen().catch(() => {});
      previous?.focus();
    };
  }, []);

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (overlay.current?.requestFullscreen) await overlay.current.requestFullscreen();
      else setMessage("This browser supports the immersive view only.");
    } catch { setMessage("Fullscreen is unavailable in this browser. The immersive view is still open."); }
    reveal();
  }

  async function command(action: string) {
    setPending(true); setMessage("");
    try { await props.onCommand(action); }
    catch (error) { setMessage((error as Error).message); }
    finally { setPending(false); reveal(); }
  }
  const { live, status, playing } = signal;
  const action = playing ? "pause" : "play";

  return (
    <div ref={overlay} className={`immersive ${visible ? "hud-visible" : "hud-hidden"}`} role="dialog" aria-modal="true" aria-label="Audio visualizer" tabIndex={-1}
      onPointerMove={reveal} onPointerDown={reveal} onFocusCapture={reveal}>
      <canvas ref={canvas} className="immersive-canvas" aria-label={`${scene} audio visualization`} role="img" />
      <div className="immersive-shade" />
      <section className="immersive-track-overlay" aria-label="Now playing overlay">
        <div className="immersive-art"><ArtworkImage src={props.artwork} alt={`${props.title} cover artwork`} /><AudioLines size={24} aria-hidden="true" /></div>
        <div className="immersive-track-copy"><h2>{props.title || "Nothing playing"}</h2><p>{props.artist || "Choose a source to start listening"}</p></div>
      </section>
      <header className="immersive-top immersive-hud">
        <div className="immersive-brand"><AudioLines size={22} /><span>LosSpeaker<span className="immersive-subtitle">SOUND IN MOTION</span></span></div>
        <div className="immersive-window-controls">
          <button aria-label={fullscreen ? "Leave browser fullscreen" : "Enter browser fullscreen"} onClick={toggleFullscreen}>{fullscreen ? <Minimize2 size={19} /> : <Maximize2 size={19} />}</button>
          <button aria-label="Exit full screen" onClick={props.onClose}><X size={22} /></button>
        </div>
      </header>
      <div className="immersive-caption immersive-hud"><span className={live ? "signal-dot live" : "signal-dot"} /><span role="status">{status}</span><span className="immersive-caption-divider" /><span>{scene} / {palette}</span></div>
      <div className="immersive-bottom immersive-hud">
        {message && <p className="immersive-error" role="alert">{message}</p>}
        <div className="immersive-toolbar">
          <div className="immersive-scenes" role="group" aria-label="Visualization style">{scenes.map((name, i) => <button key={name} aria-pressed={scene === name} onClick={() => setScene(name)}><span className="scene-number">0{i + 1}</span>{name}</button>)}</div>
          <div className="immersive-palettes" role="group" aria-label="Visualizer colours">{(Object.keys(palettes) as Palette[]).map((name) => <button key={name} aria-label={`${name} colours`} title={name} aria-pressed={palette === name} onClick={() => setPalette(name)}><span style={{ background: `linear-gradient(135deg, ${palettes[name].join(",")})` }} /></button>)}</div>
        </div>
        <footer className="immersive-player">
          <div className="immersive-transport">
            <button aria-label="Previous track" disabled={pending || !props.controls.includes("previous")} onClick={() => command("previous")}><SkipBack size={18} fill="currentColor" /></button>
            <button className="immersive-play" aria-label={playing ? "Pause" : "Play"} disabled={pending || !props.controls.includes(action)} onClick={() => command(action)}>{playing ? <Pause size={19} fill="currentColor" /> : <Play size={19} fill="currentColor" />}</button>
            <button aria-label="Next track" disabled={pending || !props.controls.includes("next")} onClick={() => command("next")}><SkipForward size={18} fill="currentColor" /></button>
          </div>
          <span className="immersive-source">{props.source}</span>
        </footer>
        <span className="immersive-hint">Move or tap to show controls · Esc to close</span>
      </div>
    </div>
  );
}
