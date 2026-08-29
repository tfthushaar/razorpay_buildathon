import { useEffect, useState } from "react";

/**
 * Minimal hash routing, in place of a router dependency.
 *
 * The app has five views and no nested routes, no route params and no data loaders, so react-router
 * would be ~10 KB and a build-config surface for something this hook does in twenty lines. Hash
 * routes also need no server rewrites: a deep link like /#/autonomy is served by the same static
 * index.html on any host, including a plain file:// open of dist/.
 *
 * Back and forward work because the hash is real browser history.
 */
export function useHashRoute<T extends string>(fallback: T, valid: readonly T[]): [T, (next: T) => void] {
  const read = (): T => {
    const raw = window.location.hash.replace(/^#\/?/, "").split("?")[0];
    return (valid as readonly string[]).includes(raw) ? (raw as T) : fallback;
  };

  const [route, setRoute] = useState<T>(read);

  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const navigate = (next: T) => {
    if (next === route) return;
    window.location.hash = `/${next}`;
    // Landing mid-page after a nav is disorienting when each view is its own page.
    window.scrollTo({ top: 0, behavior: "auto" });
  };

  return [route, navigate];
}
