import { Icon } from "../../src/components/UI";
import mascot from "../../../../site/themes/mutating/static/muta-v2-transparent-v10.webp";
import relay from "../../../../site/themes/mutating/static/pillar-verifiability-v1-lossless.webp";
import field from "../../../../site/themes/mutating/static/thesis-resistance-field-pink-v1-lossless.webp";
import { machines, stats, type Design, type OriginalDesign, type Theme } from "./model";
import {
  Access, Action, Activity, AvatarStack, CapacityMeter, Columns, Computers, Invitations, LoadChart, Metrics,
  OrganizationMark, SectionNavigation, Settings, SettingsLink, Shortcuts, Status,
  TabbedWorkspace, Team, Title, useWorkspace,
} from "./parts";
import { Refinement } from "./round-two";
import { LightRefinement } from "./round-three";

function Lead({ compact = false }: { compact?: boolean }) {
  return <header className="work-lead"><Title detail={!compact} /><Shortcuts compact={compact} /></header>;
}

function Orbit() {
  const { open } = useWorkspace();
  return <div className="network-orbit" aria-label="Компьютеры вычислительного пула">
    <div className="orbit-lines" aria-hidden="true"><i /><i /><i /><i /><i /></div>
    <OrganizationMark className="orbit-center" />
    {machines.map((machine) => <button type="button" key={machine.id} className={`orbit-node orbit-${machine.id}`} onClick={() => open({ kind: "computer", id: machine.id })}>
      <Icon name="computer" size={17} /><span>{machine.name}</span><Status online={machine.online} />
    </button>)}
  </div>;
}

function Portals() {
  const { open, workspace } = useWorkspace();
  const entries = [
    { kind: "members", icon: "users", name: "Участники", value: workspace.members.length, detail: "Команда и роли" },
    { kind: "computers", icon: "computer", name: "Компьютеры", value: machines.length, detail: `${stats.online} сейчас в сети` },
    { kind: "tokens", icon: "key", name: "API-токены", value: undefined, detail: "Доступ к ресурсам" },
  ] as const;
  return <div className="portal-links">
    {entries.map((entry) => <button type="button" key={entry.kind} onClick={() => open({ kind: entry.kind })}>
      <span className="portal-icon"><Icon name={entry.icon} size={22} /></span><strong>{entry.name}</strong><small>{entry.detail}</small>
      {entry.value !== undefined && <span className="portal-count">{entry.value}</span>}
    </button>)}
  </div>;
}

function Layout({ design }: { design: OriginalDesign }) {
  const { open, workspace } = useWorkspace();
  switch (design.key) {
    case "cover":
      return <>
        <header className="cover-heading"><div><Title accent /><Shortcuts /></div><Metrics className="cover-metrics" /></header>
        <Columns left={<Team mode="table" />} right={<><Invitations /><Access /></>} />
        <Computers details />
      </>;
    case "laboratory":
      return <>
        <header className="laboratory-heading"><div><Title accent /><Shortcuts compact /></div><div className="laboratory-art" aria-hidden="true"><img src={mascot} alt="" /></div></header>
        <Columns className="laboratory-columns" left={<><Metrics kinds={["computers", "cpu", "memory"]} /><Computers details /></>} right={<><Team compact /><Invitations /></>} />
      </>;
    case "dispatch":
      return <>
        <Lead compact />
        <div className="dispatch-grid"><LoadChart /><div className="column"><Metrics kinds={["cpu", "memory"]} /><Computers /></div></div>
        <Columns left={<Team mode="table" />} right={<Invitations />} />
      </>;
    case "ledger":
      return <>
        <header className="ledger-heading"><OrganizationMark /><Title detail={false} /><Shortcuts compact /></header>
        <SectionNavigation />
        <Team mode="table" searchable />
        <Columns left={<Computers details />} right={<Invitations />} />
        <Settings />
      </>;
    case "network":
      return <>
        <header className="network-heading"><div><Status>{stats.online} компьютера в сети</Status><Title accent /><Shortcuts primary="computers" /></div><Orbit /></header>
        <Metrics kinds={["computers", "cpu", "memory"]} className="network-metrics" />
        <Columns left={<Computers details />} right={<><Team compact /><Invitations /></>} />
      </>;
    case "team":
      return <>
        <header className="team-heading"><div><Title accent /><AvatarStack /></div><Shortcuts /></header>
        <Team mode="cards" searchable />
        <Metrics kinds={["computers", "cpu", "invites"]} />
        <Columns left={<Invitations />} right={<Computers />} />
      </>;
    case "capacity":
      return <>
        <Lead compact />
        <div className="capacity-grid"><CapacityMeter /><LoadChart compact /></div>
        <Computers cards details />
        <Columns left={<Team mode="table" />} right={<><Invitations /><Access /></>} />
      </>;
    case "monolith":
      return <div className="monolith-grid">
        <header className="monolith-rail"><OrganizationMark /><Title accent detail={false} /><Shortcuts compact /><SectionNavigation /></header>
        <div className="column"><Team /><Computers details /><Invitations /><Settings /></div>
      </div>;
    case "journal":
      return <>
        <header className="journal-heading"><div><Title accent /><Shortcuts compact /></div><div className="journal-teaser"><span className="overline">Сегодня, 10:42</span><strong>Отправлено приглашение</strong><small>Алиса Волкова</small></div></header>
        <Columns className="journal-columns" left={<><Activity /><Invitations /></>} right={<><Team compact /><Computers /></>} />
      </>;
    case "terminal":
      return <>
        <header className="terminal-heading"><div className="terminal-path"><span aria-hidden="true">›</span><span>Mutating Compute / Организации</span><Status>{stats.online} онлайн</Status></div><Lead compact /></header>
        <Metrics kinds={["computers", "cpu", "memory", "team"]} />
        <Columns className="terminal-columns" left={<Computers details />} right={<Team mode="table" compact />} />
        <Columns left={<Invitations />} right={<Access detailed />} />
      </>;
    case "pink":
      return <>
        <header className="pink-heading"><div><Title accent /><Shortcuts compact /></div><OrganizationMark className="pink-stamp" /></header>
        <Metrics className="pink-float" />
        <Columns left={<><Team mode="cards" /><Computers /></>} right={<><Invitations /><Access /></>} />
      </>;
    case "grid":
      return <>
        <header className="grid-heading"><Title /><div><span className="overline">Mutating Compute</span><Status>{stats.online} в сети</Status></div></header>
        <Metrics kinds={["team", "computers", "cpu", "memory"]} />
        <div className="grid-actions"><Shortcuts /><OrganizationMark /></div>
        <Team mode="table" searchable />
        <Columns left={<Computers details />} right={<Invitations />} />
      </>;
    case "atlas":
      return <>
        <header className="atlas-heading"><div><Title accent /><Shortcuts primary="computers" compact /></div><div className="atlas-art" aria-hidden="true"><img src={relay} alt="" /></div></header>
        <Computers cards details />
        <Columns left={<><CapacityMeter /><Team /></>} right={<><LoadChart compact /><Invitations /></>} />
      </>;
    case "portal":
      return <>
        <header className="portal-heading"><div className="portal-halo" aria-hidden="true"><OrganizationMark /></div><Title /><Shortcuts compact /></header>
        <Portals />
        <Columns left={<Team mode="table" />} right={<><Invitations /><Computers /></>} />
      </>;
    case "columns":
      return <>
        <Lead />
        <div className="columns-rule"><span><Icon name="users" size={19} /> Команда</span><span><Icon name="compute" size={19} /> Вычислительный пул</span></div>
        <Columns className="balanced-columns" left={<><Team /><Invitations /></>} right={<><Computers details /><Access detailed /></>} />
      </>;
    case "tabs":
      return <>
        <header className="tabs-heading"><OrganizationMark /><Title detail={false} /><Shortcuts /></header>
        <TabbedWorkspace />
      </>;
    case "bento":
      return <div className="mosaic-grid">
        <header className="mosaic-heading"><Title accent /><Shortcuts compact /></header>
        <Team compact />
        <LoadChart compact />
        <Invitations />
        <Computers details />
        <Access />
      </div>;
    case "profile":
      return <div className="profile-grid">
        <header className="profile-card"><div className="profile-cover"><OrganizationMark /></div><Title detail={false} /><AvatarStack />
          <div className="profile-facts"><span>Компьютеры в сети</span><b>{stats.online} из {machines.length}</b></div><Shortcuts compact />
          <Action icon="lock" tone="quiet" onClick={() => open({ kind: "settings" })}>Настройки</Action>
        </header>
        <div className="column"><Team mode="table" /><Invitations /><Computers /></div>
      </div>;
    case "panorama":
      return <>
        <header className="panorama-heading"><img src={field} alt="" /><div><Status>{stats.online} машины в сети</Status><Title accent /><Shortcuts /></div></header>
        <Metrics className="panorama-metrics" kinds={["cpu", "memory", "team"]} />
        <Columns left={<><Team mode="table" /><Invitations /></>} right={<><Computers details /><Access /></>} />
      </>;
    case "layers":
      return <>
        <header className="layers-heading"><div className="layers-main"><Title accent /><Shortcuts compact /></div><div className="layers-id"><OrganizationMark /><span className="overline">{workspace.members.length} участника</span><Status>{stats.online} компьютера в сети</Status></div></header>
        <Columns className="layers-columns" left={<><Team /><Invitations /></>} right={<><CapacityMeter /><Access /><Computers /></>} />
      </>;
  }
}

export function OrganizationDesign({ design, theme }: { design: Design; theme: Theme }) {
  return <article className={`work-design design-${design.key}`} data-design={design.number} data-theme={theme}>
    {"family" in design ? <LightRefinement design={design} /> : "base" in design ? <Refinement design={design} /> : <Layout design={design} />}
    {!["ledger", "monolith", "tabs", "profile", "atlas-tabs", "atlas-index", "mural-tabs"].includes(design.key) && <SettingsLink />}
  </article>;
}
