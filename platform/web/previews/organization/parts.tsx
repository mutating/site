import { createContext, useContext, useId, useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "../../src/components/UI";
import { activity, initials, machines, stats, tokens, type Member, type Workspace } from "./model";

export type Section = "team" | "machines" | "invitations" | "settings";
export type DialogState =
  | { kind: "invite" | "invites" | "members" | "computers" | "tokens" | "settings" | "rename" | "leave" | "delete" }
  | { kind: "member" | "computer" | "invitation"; id: string };

export const WorkspaceContext = createContext<{
  workspace: Workspace;
  open: (dialog: DialogState) => void;
  section: Section;
  setSection: (section: Section) => void;
} | null>(null);

export function useWorkspace() {
  return useContext(WorkspaceContext)!;
}

export function Action({ children, icon, onClick, tone = "outline", label }: {
  children?: ReactNode;
  icon?: string;
  onClick: () => void;
  tone?: "primary" | "outline" | "quiet" | "danger";
  label?: string;
}) {
  return <button type="button" className={`work-action action-${tone}`} aria-label={label} onClick={onClick}>
    {icon && <Icon name={icon} size={16} />}{children}
  </button>;
}

export function Shortcuts({ primary = "invite", compact = false }: { primary?: "invite" | "computers"; compact?: boolean }) {
  const { open } = useWorkspace();
  return <div className="shortcuts">
    <Action icon={primary === "invite" ? "plus" : "computer"} tone="primary" onClick={() => open({ kind: primary })}>
      {primary === "invite" ? "Пригласить" : "Список компьютеров"}
    </Action>
    {!compact && <Action icon={primary === "invite" ? "computer" : "plus"} onClick={() => open({ kind: primary === "invite" ? "computers" : "invite" })}>
      {primary === "invite" ? "Компьютеры" : "Пригласить"}
    </Action>}
    <Action icon="key" onClick={() => open({ kind: "tokens" })}>API-токены</Action>
  </div>;
}

export function OrganizationMark({ className = "" }: { className?: string }) {
  const { workspace } = useWorkspace();
  return <span className={`organization-mark ${className}`} aria-hidden="true">{initials(workspace.name)}</span>;
}

export function Title({ accent = false, detail = true }: { accent?: boolean; detail?: boolean }) {
  const { workspace } = useWorkspace();
  const lineBreak = workspace.name.indexOf(" ");
  return <div className="workspace-title">
    <div className="title-meta"><span className="overline">Организация</span><span className="role-badge role-creator">Создатель</span></div>
    <h1 tabIndex={-1}>{accent && lineBreak > 0
      ? <>{workspace.name.slice(0, lineBreak)}{" "}<br /><em>{workspace.name.slice(lineBreak + 1)}</em></>
      : workspace.name}<span className="title-dot">.</span></h1>
    {detail && <p>Общее рабочее пространство команды</p>}
  </div>;
}

export function Status({ online = true, children }: { online?: boolean; children?: ReactNode }) {
  return <span className={`work-status${online ? "" : " is-offline"}`}><i aria-hidden="true" />{children ?? (online ? "В сети" : "Офлайн")}</span>;
}

export function Avatar({ member }: { member: Member }) {
  return <span className={`work-avatar avatar-${member.color}`} aria-hidden="true">{initials(member.name)}</span>;
}

export function AvatarStack() {
  const { workspace, open } = useWorkspace();
  return <button type="button" className="avatar-stack" onClick={() => open({ kind: "members" })}>
    <span className="avatar-stack-pictures">{workspace.members.map((member) => <Avatar member={member} key={member.id} />)}</span>
    <span>Команда <small>{workspace.members.length}</small></span>
  </button>;
}

export function Metrics({ kinds = ["team", "computers", "invites"], className = "" }: {
  kinds?: readonly ("team" | "computers" | "invites" | "cpu" | "memory")[];
  className?: string;
}) {
  const { workspace, open } = useWorkspace();
  const measures = {
    team: { label: "Участники", value: workspace.members.length, icon: "users", detail: "В команде", action: "members" },
    computers: { label: "Компьютеры", value: machines.length, icon: "computer", detail: `${stats.online} в сети`, action: "computers" },
    invites: { label: "Приглашения", value: workspace.invitations.length, icon: "plus", detail: "Ждут ответа", action: "invites" },
    cpu: { label: "Свободный CPU", value: stats.freeCpu, icon: "cpu", detail: `из ${stats.totalCpu} ядер`, action: "computers" },
    memory: { label: "Свободная память", value: stats.freeMemory, icon: "memory", detail: `из ${stats.totalMemory} ГиБ`, action: "computers" },
  } as const;
  return <div className={`work-metrics ${className}`}>
    {kinds.map((kind) => {
      const metric = measures[kind];
      return <button type="button" className="work-metric" key={kind} onClick={() => open({ kind: metric.action })}>
        <span className="metric-caption"><Icon name={metric.icon} size={16} />{metric.label}</span>
        <strong>{metric.value}</strong><small>{metric.detail}</small>
      </button>;
    })}
  </div>;
}

export function Panel({ title, count, action, children, className = "", section }: {
  title: string;
  count?: number;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  section?: Section;
}) {
  return <section className={`work-panel ${className}`} data-section={section} aria-label={title}>
    <header className="panel-heading"><h2>{title}{count !== undefined && <span className="section-count">{count}</span>}</h2>{action}</header>
    {children}
  </section>;
}

export function Columns({ left, right, className = "" }: { left: ReactNode; right: ReactNode; className?: string }) {
  return <div className={`work-columns ${className}`}><div className="column">{left}</div><div className="column">{right}</div></div>;
}

export function CapacityMeter() {
  return <Panel title="Доступные мощности" className="capacity-meter" action={<Icon name="cpu" size={20} />}>
    <div className="capacity-amount"><strong>{stats.freeCpu}</strong><span>/ {stats.totalCpu} ядер</span></div>
    <div className="meter-track" role="meter" aria-label="Свободные ядра" aria-valuemin={0} aria-valuemax={stats.totalCpu} aria-valuenow={stats.freeCpu}><i style={{ width: `${stats.freeCpu / stats.totalCpu * 100}%` }} /></div>
    <div className="capacity-memory"><span>Память</span><b>{stats.freeMemory} / {stats.totalMemory} ГиБ</b></div>
    <div className="meter-track meter-memory" role="meter" aria-label="Свободная память" aria-valuemin={0} aria-valuemax={stats.totalMemory} aria-valuenow={stats.freeMemory}><i style={{ width: `${stats.freeMemory / stats.totalMemory * 100}%` }} /></div>
  </Panel>;
}

export function MemberList({ members, mode = "rows", label = true }: { members: Member[]; mode?: "rows" | "table" | "cards"; label?: boolean }) {
  const { open } = useWorkspace();
  return <div className={`member-list member-list-${mode}`}>
    {mode === "table" && <div className="member-table-labels" aria-hidden="true"><span>Участник</span><span>Роль</span><span>Доступ</span></div>}
    {members.map((member) => <button type="button" className={`person person-${member.color}`} key={member.id} onClick={() => open({ kind: "member", id: member.id })} aria-label={`Права: ${member.name}`}>
      <span className="person-identity"><Avatar member={member} /><span><strong>{member.name}</strong>{label && <small>@{member.handle}</small>}</span></span>
      <span className={`role-badge${member.role === "Создатель" ? " role-creator" : ""}`}>{member.role}</span>
      {mode === "table" && <span className="person-rights">{member.role === "Участник" ? "Ресурсы" : "Управление"}</span>}
    </button>)}
    {!members.length && <p className="empty-line">Участники не найдены.</p>}
  </div>;
}

export function Team({ mode = "rows", searchable = false, compact = false }: {
  mode?: "rows" | "table" | "cards";
  searchable?: boolean;
  compact?: boolean;
}) {
  const { workspace, open } = useWorkspace();
  const [filter, setFilter] = useState("");
  const visibleMembers = workspace.members.filter((member) => `${member.name} ${member.handle}`.toLocaleLowerCase("ru").includes(filter.trim().toLocaleLowerCase("ru")));
  return <Panel title="Участники" count={workspace.members.length} section="team" className="team-panel"
    action={searchable
      ? <label className="member-search"><Icon name="search" size={15} /><input type="search" aria-label="Найти участника" placeholder="Поиск по имени" value={filter} onChange={(event) => setFilter(event.currentTarget.value)} /></label>
      : <Action icon="plus" tone="quiet" onClick={() => open({ kind: "invite" })}>Добавить</Action>}>
    <MemberList members={visibleMembers} mode={mode} label={!compact} />
  </Panel>;
}

export function ComputerList({ cards = false, details = false }: { cards?: boolean; details?: boolean }) {
  const { open } = useWorkspace();
  return <div className={`computer-list${cards ? " computer-cards" : ""}`}>
    {machines.map((machine) => <button type="button" className="machine" key={machine.id} onClick={() => open({ kind: "computer", id: machine.id })}>
      <span className="machine-icon"><Icon name="computer" size={21} /></span>
      <span className="machine-identity"><strong>{machine.name}</strong><small>{machine.platform}</small></span>
      <Status online={machine.online} />
      {details && <span className="machine-resources"><small>CPU свободно</small><b>{machine.cpu} / {machine.cores}</b></span>}
    </button>)}
  </div>;
}

export function Computers({ cards = false, details = false }: { cards?: boolean; details?: boolean }) {
  const { open } = useWorkspace();
  return <Panel title="Компьютеры" count={machines.length} section="machines" className="machines-panel"
    action={<Action tone="quiet" onClick={() => open({ kind: "computers" })}>Открыть список</Action>}>
    <ComputerList cards={cards} details={details} />
  </Panel>;
}

export function Invitations() {
  const { workspace, open } = useWorkspace();
  return <Panel title="Приглашения" count={workspace.invitations.length} className="invitations-panel" section="invitations"
    action={<Action icon="plus" tone="quiet" onClick={() => open({ kind: "invite" })}>Пригласить</Action>}>
    <div className="invite-list">
      {workspace.invitations.map((invite) => <div className="invite-row" key={invite.id}>
        <span className="invite-contact"><strong>{invite.contact}</strong><small>{invite.role} · {invite.sent}</small></span>
        <Action tone="quiet" label={`Отозвать приглашение для ${invite.contact}`} onClick={() => open({ kind: "invitation", id: invite.id })}>Отозвать</Action>
      </div>)}
      {!workspace.invitations.length && <p className="empty-line">Приглашений пока нет.</p>}
    </div>
  </Panel>;
}

export function TokenList() {
  return <div className="token-list">
    {tokens.map((token) => <div className="token-row" key={token.name}>
      <Icon name="key" size={17} /><span><strong>{token.name}</strong><small>{token.scope}</small></span><span className="token-expiry">{token.expiry}</span>
    </div>)}
  </div>;
}

export function Access({ detailed = false }: { detailed?: boolean }) {
  const { open } = useWorkspace();
  return <Panel title="Доступ" className="access-panel" action={<Icon name="key" size={19} />}>
    {detailed ? <TokenList /> : <p className="panel-note">{tokens.length} активных API-токена</p>}
    <Action icon="key" tone="quiet" onClick={() => open({ kind: "tokens" })}>API-токены</Action>
  </Panel>;
}

export function Settings() {
  const { workspace, open } = useWorkspace();
  return <Panel title="Настройки" className="settings-panel" section="settings" action={<Icon name="lock" size={18} />}>
    <div className="settings-row"><span><small>Название организации</small><strong>{workspace.name}</strong></span><Action onClick={() => open({ kind: "rename" })}>Изменить</Action></div>
    <div className="settings-risk"><Action icon="exit" tone="quiet" onClick={() => open({ kind: "leave" })}>Выйти из организации</Action><Action icon="trash" tone="danger" onClick={() => open({ kind: "delete" })}>Удалить организацию</Action></div>
  </Panel>;
}

export function SettingsLink() {
  const { open } = useWorkspace();
  return <div className="settings-link"><Action tone="quiet" icon="lock" onClick={() => open({ kind: "settings" })}>Настройки организации</Action></div>;
}

export function Activity() {
  return <Panel title="Журнал событий" className="activity-panel" action={<span className="overline">Последние 2 дня</span>}>
    <ol className="activity-list">
      {activity.map((entry) => <li key={entry.time}>
        <time><strong>{entry.time}</strong><small>{entry.date}</small></time><span className="event-icon"><Icon name={entry.icon} size={17} /></span>
        <span className="event-detail"><strong>{entry.title}</strong><small>{entry.detail}</small></span>
      </li>)}
    </ol>
  </Panel>;
}

const load = [18, 25, 21, 32, 44, 37, 48, 32, 55, 49, 38, 64, 57, 41, 70, 60, 50, 68, 82, 66, 57, 71, 64, 55];

export function LoadChart({ compact = false }: { compact?: boolean }) {
  const { open } = useWorkspace();
  return <Panel title="Нагрузка CPU" className={`load-panel${compact ? " load-compact" : ""}`} action={<span className="overline">24 часа</span>}>
    <div className="load-heading"><strong>{load.at(-1)}<small> %</small></strong><Status>{stats.online} из {machines.length} в сети</Status></div>
    <div className="load-bars" role="img" aria-label="Динамика нагрузки CPU за 24 часа">
      {load.map((value, index) => <i key={index} style={{ "--bar-height": `${value}%` } as CSSProperties} />)}
    </div>
    <div className="chart-axis"><span>−24 ч</span><span>−12 ч</span><span>Сейчас</span></div>
    {!compact && <Action icon="computer" tone="quiet" onClick={() => open({ kind: "computers" })}>Список компьютеров</Action>}
  </Panel>;
}

const sections: { id: Section; label: string; icon: string }[] = [
  { id: "team", label: "Участники", icon: "users" },
  { id: "machines", label: "Компьютеры", icon: "computer" },
  { id: "invitations", label: "Приглашения", icon: "plus" },
  { id: "settings", label: "Настройки", icon: "lock" },
];

export function SectionNavigation({ tabs = false, id, machinesFirst = false }: { tabs?: boolean; id?: string; machinesFirst?: boolean }) {
  const { section, setSection } = useWorkspace();
  const entries = machinesFirst ? [sections[1], sections[0], ...sections.slice(2)] : sections;
  return <nav className="section-navigation" role={tabs ? "tablist" : undefined} aria-label="Разделы организации">
    {entries.map((entry, index) => <button type="button" key={entry.id} id={tabs ? `${id}-tab-${entry.id}` : undefined}
      role={tabs ? "tab" : undefined} aria-selected={tabs ? section === entry.id : undefined}
      aria-controls={tabs ? `${id}-section` : undefined} tabIndex={tabs && section !== entry.id ? -1 : undefined}
      onKeyDown={(event) => {
        if (!tabs || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1
          : (index + (event.key === "ArrowLeft" ? -1 : 1) + entries.length) % entries.length;
        setSection(entries[next].id);
        document.getElementById(`${id}-tab-${entries[next].id}`)?.focus();
      }}
      onClick={(event) => {
        if (tabs) setSection(entry.id);
        else event.currentTarget.closest("[data-design]")?.querySelector(`[data-section='${entry.id}']`)?.scrollIntoView({ block: "start" });
      }}><Icon name={entry.icon} size={17} />{entry.label}</button>)}
  </nav>;
}

export function TabbedWorkspace({ machinesFirst = false }: { machinesFirst?: boolean }) {
  const { section } = useWorkspace();
  const id = useId();
  const content = { team: <Team mode="table" searchable />, machines: <Computers details cards={machinesFirst} />, invitations: <Invitations />, settings: <Settings /> };
  return <>
    <SectionNavigation tabs id={id} machinesFirst={machinesFirst} />
    <div className="tab-content column" id={`${id}-section`} role="tabpanel" aria-labelledby={`${id}-tab-${section}`} tabIndex={0}>{content[section]}</div>
  </>;
}
