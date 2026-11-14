import { useEffect, useState } from "react";
import { checkHealth } from "../lib/api";

export function useConnection(): boolean {
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const probe = async () => {
      const ok = await checkHealth();
      if (!cancelled) setConnected(ok);
    };

    void probe();
    const timer = window.setInterval(() => void probe(), 20_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return connected;
}
