/**
 * A divider between groups of panels.
 *
 * Twelve panels of identical visual weight give a reader no way to tell the work product from the
 * diagnostics. These headings say what the next few panels are for, in the language of the person
 * doing the job rather than the language of the module that produces them.
 */
export function SectionHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="section-heading">
      <h2>{title}</h2>
      {subtitle && <p>{subtitle}</p>}
    </div>
  );
}
