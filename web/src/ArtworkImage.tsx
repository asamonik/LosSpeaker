import { useEffect, useRef, useState } from "react";

type Props = { src: string | null | undefined; alt: string };

export default function ArtworkImage({ src, alt }: Props) {
  if (!src || !/^(https?:|\/api\/v1\/library\/art|\/api\/v1\/artwork\/)/.test(src)) return null;
  // Changing songs discards failed requests and their pending retries.
  return <RetryingImage key={src} src={src} alt={alt} />;
}

function RetryingImage({ src, alt }: { src: string; alt: string }) {
  const [request, setRequest] = useState({ src, attempt: 0 });
  const [failed, setFailed] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);

  function retry() {
    setFailed(true);
    if (timer.current !== null) return;
    const delay = Math.min(1000 * 2 ** Math.min(request.attempt, 5), 30000);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      // Local failures must not get stuck in a browser's cached 404 response.
      // Keep external, potentially signed CDN URLs intact.
      const url = new URL(src, location.href);
      if (url.origin === location.origin) url.searchParams.set("_artwork_retry", String(Date.now()));
      setRequest({ src: url.href, attempt: request.attempt + 1 });
    }, delay);
  }

  return <img key={request.attempt} src={request.src} alt={alt}
    style={failed ? { display: "none" } : undefined}
    onError={retry} onLoad={() => setFailed(false)} />;
}
