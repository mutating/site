import type { ReactNode } from "react";
import relay from "../../../../site/themes/mutating/static/pillar-verifiability-v1-lossless.webp";
import { Devices } from "./device-cards";
import { machines, stats, type ThirdRoundDesign } from "./model";
import {
  Access, Activity, AvatarStack, CapacityMeter, Columns, Computers, Invitations, LoadChart, Metrics,
  OrganizationMark, SectionNavigation, Settings, Shortcuts, Status, TabbedWorkspace, Team, Title,
} from "./parts";

const poolMetrics = ["computers", "cpu", "memory", "team"] as const;
const resourceMetrics = ["cpu", "memory"] as const;

function AtlasImage({ className = "" }: { className?: string }) {
  return <div className={`edition-art ${className}`} aria-hidden="true"><img src={relay} alt="" /></div>;
}

function Identity({ inline = false, status = false, description = true }: { inline?: boolean; status?: boolean; description?: boolean }) {
  return <div className={`edition-identity${inline ? " edition-identity-inline" : ""}`}>
    <div>
      {status && <Status>{stats.online} из {machines.length} компьютеров в сети</Status>}
      <Title accent={!inline} detail={description} />
    </div>
    <Shortcuts compact />
  </div>;
}

function LightCover({ inline = false, status = false, mark = false, description = true, children }: {
  inline?: boolean; status?: boolean; mark?: boolean; description?: boolean; children?: ReactNode;
}) {
  return <header className="edition-cover">
    <AtlasImage />
    <Identity inline={inline} status={status} description={description} />
    {mark && <OrganizationMark />}
    {children}
  </header>;
}

function MuralCover({ identityFirst = false, children }: { identityFirst?: boolean; children?: ReactNode }) {
  const title = <Identity inline />;
  return <header className="mural-cover">
    {identityFirst && title}
    <AtlasImage className="mural-art" />
    {!identityFirst && title}
    {children}
  </header>;
}

export function LightRefinement({ design }: { design: ThirdRoundDesign }) {
  switch (design.key) {
    case "atlas-clear":
      return <>
        <LightCover mark />
        <Metrics kinds={poolMetrics} className="edition-summary" />
        <Columns left={<><Computers cards details /><Invitations /></>} right={<><Team compact /><Access /></>} />
      </>;
    case "atlas-porcelain":
      return <>
        <LightCover status />
        <div className="edition-desk"><CapacityMeter /><LoadChart compact /></div>
        <Columns left={<><Computers details /><Invitations /></>} right={<Team compact />} />
      </>;
    case "atlas-dashboard":
      return <>
        <LightCover inline description={false} status />
        <div className="edition-desk"><CapacityMeter /><LoadChart /></div>
        <Columns left={<><Computers details /><Activity /></>} right={<><Team /><Invitations /></>} />
      </>;
    case "atlas-overlap":
      return <>
        <div className="edition-overlap"><LightCover status /><Devices compact /></div>
        <Columns left={<><Team mode="table" /><Invitations /></>} right={<><CapacityMeter /><Access /></>} />
      </>;
    case "atlas-lines":
      return <>
        <LightCover mark inline />
        <Metrics kinds={poolMetrics} className="edition-summary" />
        <Columns left={<><Computers details /><Invitations /></>} right={<><Team mode="table" /><Access /></>} />
      </>;
    case "atlas-index":
      return <>
        <LightCover inline description={false} />
        <SectionNavigation />
        <Computers details />
        <Columns left={<Team mode="table" searchable />} right={<><Invitations /><Access detailed /></>} />
        <Settings />
      </>;
    case "atlas-racks":
      return <>
        <LightCover inline status />
        <Devices />
        <Columns left={<><LoadChart compact /><Invitations /></>} right={<><Team compact /><Access /></>} />
      </>;
    case "atlas-members":
      return <>
        <LightCover><AvatarStack /></LightCover>
        <Columns className="edition-team-first" left={<><Team mode="table" searchable /><Invitations /></>} right={<><Metrics kinds={resourceMetrics} /><Computers details /><Access /></>} />
      </>;
    case "mural-wide":
      return <>
        <MuralCover />
        <Columns left={<><Computers details /><Invitations /></>} right={<><CapacityMeter /><Team compact /></>} />
      </>;
    case "mural-strip":
      return <>
        <MuralCover />
        <Columns className="mural-directories" left={<><Computers details /><Invitations /></>} right={<><Team compact /><Metrics kinds={resourceMetrics} /></>} />
      </>;
    case "mural-caption":
      return <>
        <MuralCover />
        <Metrics kinds={poolMetrics} className="edition-summary" />
        <Columns left={<><Computers cards details /><CapacityMeter /></>} right={<><Team compact /><Invitations /></>} />
      </>;
    case "mural-inset":
      return <>
        <MuralCover identityFirst><Metrics className="edition-summary" kinds={poolMetrics} /></MuralCover>
        <Columns left={<><Computers details /><Invitations /></>} right={<><Team compact /><Access /></>} />
      </>;
    case "mural-tabs":
      return <>
        <MuralCover identityFirst />
        <TabbedWorkspace machinesFirst />
      </>;
    case "mural-triptych":
      return <>
        <header className="mural-cover">
          <div className="mural-triptych"><AtlasImage /><AtlasImage /><AtlasImage /></div>
          <Identity inline />
        </header>
        <Columns left={<><Computers details /><Activity /></>} right={<><Team compact /><Invitations /></>} />
      </>;
    case "mural-metrics":
      return <>
        <header className="mural-cover">
          <div className="mural-metric-plate"><AtlasImage className="mural-art" /><Metrics kinds={poolMetrics} className="edition-summary" /></div>
          <Identity inline />
        </header>
        <Columns left={<><Computers details /><Invitations /></>} right={<><Team mode="table" /><Access /></>} />
      </>;
    case "mural-lower":
      return <>
        <header className="mural-cover"><Identity inline /></header>
        <Columns className="mural-pool-first" left={<Computers details />} right={<CapacityMeter />} />
        <AtlasImage className="mural-art" />
        <Columns left={<><Team mode="table" /><Access /></>} right={<Invitations />} />
      </>;
    case "pink-trim":
      return <>
        <MuralCover />
        <Columns left={<><Computers details /><Invitations /></>} right={<><Metrics kinds={resourceMetrics} /><Team compact /></>} />
      </>;
    case "pink-metrics":
      return <>
        <LightCover mark />
        <Metrics kinds={poolMetrics} className="edition-summary pink-surface" />
        <Columns left={<><Computers cards details /><Invitations /></>} right={<><Team compact /><Access /></>} />
      </>;
    case "pink-relief":
      return <>
        <MuralCover />
        <Columns className="edition-team-first" left={<><Team mode="table" /><Invitations /></>} right={<><Computers cards details /><Access /></>} />
      </>;
    case "pink-capacity":
      return <>
        <LightCover inline status />
        <Columns className="pink-capacity-grid" left={<><Computers details /><Team mode="table" /></>} right={<><div className="pink-surface pink-capacity-meter"><CapacityMeter /></div><Invitations /><Access /></>} />
      </>;
  }
}
