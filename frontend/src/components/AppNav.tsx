import type { PageId } from "../pages";
import { PAGES } from "../pages";

/**
 * Primary navigation.
 *
 * Pages are grouped by the question a reader is asking, not by which module produced the panel:
 * what did you learn, what does it do, how much does it do alone, how do I know it works, can I
 * break it. That ordering also happens to be the order a sceptical reviewer asks them in.
 */
export function AppNav({ page, onNavigate }: { page: PageId; onNavigate: (p: PageId) => void }) {
  return (
    <nav className="app-nav" aria-label="Sections">
      {PAGES.map((p) => (
        <a
          key={p.id}
          href={`#/${p.id}`}
          className={`app-nav-tab${p.id === page ? " is-active" : ""}`}
          aria-current={p.id === page ? "page" : undefined}
          onClick={(e) => {
            e.preventDefault();
            onNavigate(p.id);
          }}
        >
          <span className="app-nav-label">{p.label}</span>
          <span className="app-nav-hint">{p.question}</span>
        </a>
      ))}
    </nav>
  );
}
