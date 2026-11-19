import type { ReactNode } from "react";
import { Icon } from "../../src/components/UI";
import relay from "../../../../site/themes/mutating/static/pillar-verifiability-v1-lossless.webp";
import field from "../../../../site/themes/mutating/static/thesis-resistance-field-pink-v1-lossless.webp";
import { Devices } from "./device-cards";
import { machines, stats, type RefinedDesign } from "./model";
import {
  Access, Activity, AvatarStack, CapacityMeter, Columns, Computers, Invitations, LoadChart, Metrics,
  OrganizationMark, Shortcuts, Status, TabbedWorkspace, Team, Title, useWorkspace,
} from "./parts";

function Artwork({ type = "atlas" }: { type?: "atlas" | "field" }) {
  return <div className={`study-art study-art-${type}`} aria-hidden="true"><img src={type === "atlas" ? relay : field} alt="" /></div>;
}

function Identity({ split = true, description = true, showStatus = false, children }: {
  split?: boolean; description?: boolean; showStatus?: boolean; children?: ReactNode;
}) {
  return <div className="study-identity">
    {showStatus && <Status>{stats.online} из {machines.length} компьютеров в сети</Status>}
    <Title accent={split} detail={description} />
    {children ?? <Shortcuts compact />}
  </div>;
}

function NodeMap() {
  const { open } = useWorkspace();
  return <div className="study-node-map">
    <Artwork />
    <div className="study-node-list" aria-label="Компьютеры вычислительного пула">
      {machines.map((machine) => <button key={machine.id} type="button" onClick={() => open({ kind: "computer", id: machine.id })}>
        <Icon name="computer" size={18} /><strong>{machine.name}</strong><Status online={machine.online} />
      </button>)}
    </div>
  </div>;
}

export function Refinement({ design }: { design: RefinedDesign }) {
  switch (design.key) {
    case "atlas-focus":
      return <>
        <div className="atlas-focus-frame">
          <header className="study-heading study-on-dark"><Artwork /><Identity showStatus /></header>
          <Devices compact />
        </div>
        <Columns left={<><CapacityMeter /><LoadChart compact /></>} right={<><Team compact /><Invitations /></>} />
      </>;
    case "atlas-spread":
      return <>
        <header className="study-heading study-split"><Identity /><Artwork /></header>
        <Columns className="study-spread-body" left={<><Computers details /><Invitations /></>} right={<><CapacityMeter /><Team compact /></>} />
      </>;
    case "atlas-rail":
      return <div className="study-rail">
        <header className="study-heading study-on-dark"><Artwork /><OrganizationMark /><Identity /></header>
        <div className="column"><Metrics kinds={["computers", "cpu", "memory"]} /><Computers details /><Team compact /><Invitations /></div>
      </div>;
    case "atlas-display":
      return <>
        <div className="atlas-display-frame">
          <header className="study-heading study-on-dark"><Artwork /><Identity showStatus /></header>
          <Devices />
        </div>
        <Columns left={<><Team mode="table" /><Invitations /></>} right={<><LoadChart compact /><Access /></>} />
      </>;
    case "atlas-console":
      return <>
        <div className="study-console">
          <header className="study-heading study-on-dark"><Artwork /><Identity description={false} showStatus /></header>
          <LoadChart compact />
        </div>
        <Columns left={<><Computers details /><Team compact /></>} right={<><CapacityMeter /><Invitations /></>} />
      </>;
    case "atlas-light":
      return <>
        <header className="study-heading"><Artwork /><Identity /><OrganizationMark /></header>
        <Metrics kinds={["computers", "cpu", "memory", "team"]} />
        <Columns left={<><Computers cards details /><Invitations /></>} right={<><Team compact /><Access /></>} />
      </>;
    case "atlas-devices":
      return <>
        <header className="study-heading"><Artwork /><Identity split={false} description={false} showStatus /></header>
        <Devices />
        <Columns left={<><LoadChart compact /><Invitations /></>} right={<><Team compact /><Access /></>} />
      </>;
    case "atlas-nodes":
      return <>
        <header className="study-heading study-on-dark study-split"><Identity showStatus /><NodeMap /></header>
        <Columns left={<><Computers details /><CapacityMeter /></>} right={<><Team compact /><Invitations /></>} />
      </>;
    case "atlas-pink":
      return <>
        <header className="study-heading study-pink study-split"><Identity /><Artwork /></header>
        <Metrics className="study-ribbon" kinds={["computers", "team", "invites"]} />
        <Columns left={<><Computers cards details /><CapacityMeter /></>} right={<><Team compact /><Invitations /></>} />
      </>;
    case "atlas-tabs":
      return <>
        <header className="study-heading study-on-dark"><Artwork /><Identity split={false} description={false} /></header>
        <TabbedWorkspace machinesFirst />
      </>;
    case "atlas-active":
      return <>
        <header className="study-heading study-on-dark"><Artwork /><Identity split={false} description={false} showStatus /></header>
        <Devices featured />
        <Columns left={<><Activity /><Invitations /></>} right={<><Team compact /><CapacityMeter /></>} />
      </>;
    case "atlas-mural":
      return <>
        <header className="study-heading"><Artwork /><Identity split={false} /></header>
        <Columns className="mural-body" left={<><Computers details /><Invitations /></>} right={<><Metrics kinds={["cpu", "memory"]} /><Team compact /></>} />
      </>;
    case "pink-summary":
      return <>
        <header className="study-heading study-pink"><Identity /><Metrics kinds={["team", "computers", "invites"]} /></header>
        <Columns left={<><Team mode="table" /><Invitations /></>} right={<><Computers details /><Access /></>} />
      </>;
    case "pink-contrast":
      return <>
        <div className="study-contrast">
          <header className="study-heading study-on-dark"><Identity showStatus /></header>
          <div className="study-pink"><CapacityMeter /></div>
        </div>
        <Devices compact />
        <Columns left={<Team mode="cards" />} right={<><Invitations /><Access /></>} />
      </>;
    case "pink-profile":
      return <div className="study-rail">
        <header className="study-heading study-pink"><OrganizationMark /><Identity /><AvatarStack /></header>
        <div className="column"><Team mode="table" /><Invitations /><Computers details /></div>
      </div>;
    case "pink-cascade":
      return <>
        <div className="study-cascade">
          <header className="study-heading study-pink"><Identity /><OrganizationMark /></header>
          <Columns left={<Computers details />} right={<CapacityMeter />} />
        </div>
        <Columns left={<><Team mode="table" /><Access /></>} right={<Invitations />} />
      </>;
    case "field-center":
      return <>
        <header className="study-heading study-on-dark"><Artwork type="field" /><Identity split={false} showStatus /></header>
        <Metrics className="panorama-metrics" kinds={["computers", "cpu", "memory"]} />
        <Columns left={<><Devices compact /><Invitations /></>} right={<><Team compact /><Access /></>} />
      </>;
    case "field-horizon":
      return <>
        <header className="study-heading study-on-dark"><Artwork type="field" /><Identity split={false} description={false} /></header>
        <Metrics className="panorama-metrics" kinds={["team", "computers", "cpu", "memory"]} />
        <Columns left={<><LoadChart /><Invitations /></>} right={<><Computers details /><Team compact /></>} />
      </>;
    case "field-pulse":
      return <>
        <header className="study-heading study-on-dark"><Artwork type="field" /><Identity showStatus /></header>
        <Columns className="field-pulse-body" left={<><Activity /><Invitations /></>} right={<><Computers details /><CapacityMeter /><Team compact /></>} />
      </>;
    case "field-light":
      return <>
        <header className="study-heading study-split"><Identity /><Artwork type="field" /></header>
        <div className="field-light-card"><Metrics kinds={["team", "computers", "cpu", "memory"]} /></div>
        <Columns left={<><Computers details /><CapacityMeter /></>} right={<><Team compact /><Invitations /></>} />
      </>;
  }
}
