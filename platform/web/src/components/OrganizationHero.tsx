import { useState, type ReactNode } from "react";
import organizationArtwork from "../assets/organization-atlas.webp";
import { ApiImage } from "./ApiImage";
import { usePoolPointerLayout } from "./OrganizationResources";

export function OrganizationHero({
  action, ...appearance
}: {
  name: string;
  context: string;
  description: string;
  avatar?: string | null;
  background?: string | null;
  action?: ReactNode;
}) {
  const [held, setHeld] = useState<typeof appearance | null>(null);
  const holdLayout = usePoolPointerLayout(() => {
    setHeld(appearance);
    return () => setHeld(null);
  });
  const { name, context, description, avatar, background } = held ?? appearance;
  const breakAt = name.indexOf(" ");
  return (
    <header className="organization-hero" onPointerDownCapture={holdLayout}>
      <div className={`organization-hero-art${background ? " is-custom" : ""}`} aria-hidden="true">
        <ApiImage path={background ?? null} fallback={<img src={organizationArtwork} alt="" />} />
      </div>
      <div className="organization-hero-copy">
        <div className="organization-hero-context">
          {avatar && <span className="organization-hero-avatar" aria-hidden="true"><ApiImage path={avatar} fallback={null} /></span>}
          <p className="eyebrow">{context}</p>
        </div>
        <h1 aria-label={`${name}.`}>
          {breakAt > 0 ? <>{name.slice(0, breakAt)}{" "}<br /><span>{name.slice(breakAt + 1)}</span></> : name}
          <span className="hot" aria-hidden="true">.</span>
        </h1>
        <p className="lede">{description}</p>
        {action}
      </div>
    </header>
  );
}
