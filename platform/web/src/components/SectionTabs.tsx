export function SectionTabs<Key extends string>({ id, label, options, active, onChange, vertical = false }: {
  id: string;
  label: string;
  options: readonly { key: Key; label: string; count?: string }[];
  active: Key;
  onChange: (key: Key) => void;
  vertical?: boolean;
}) {
  return (
    <div className="section-tabs" role="tablist" aria-label={label} aria-orientation={vertical ? "vertical" : "horizontal"}>
      {options.map(({ key, label, count }, index) => (
        <button
          key={key}
          type="button"
          role="tab"
          id={`${id}-tab-${key}`}
          aria-controls={`${id}-panel-${key}`}
          aria-selected={active === key}
          tabIndex={active === key ? 0 : -1}
          onClick={() => onChange(key)}
          onKeyDown={(event) => {
            if (event.altKey || event.ctrlKey || event.metaKey) return;
            const previous = vertical ? "ArrowUp" : "ArrowLeft";
            const next = vertical ? "ArrowDown" : "ArrowRight";
            let destination: number;
            if (event.key === "Home") destination = 0;
            else if (event.key === "End") destination = options.length - 1;
            else if (event.key === previous) destination = (index + options.length - 1) % options.length;
            else if (event.key === next) destination = (index + 1) % options.length;
            else return;
            event.preventDefault();
            const target = options[destination].key;
            onChange(target);
            document.getElementById(`${id}-tab-${target}`)?.focus();
          }}
        >{label}{count !== undefined && <span aria-hidden="true"> {count}</span>}</button>
      ))}
    </div>
  );
}
