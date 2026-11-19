import { useId, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Icon } from "../../src/components/UI";
import { machines, type Invitation, type Member, type Theme, type Workspace } from "./model";
import { Action, ComputerList, Invitations, MemberList, Settings, Status, TokenList, useWorkspace, type DialogState } from "./parts";

type Update = (update: (workspace: Workspace) => Workspace, message: string) => void;
type InviteRole = Invitation["role"];

const dialogTitles = {
  invite: "Доступ к организации", invites: "Приглашения", members: "Участники", member: "Роль участника",
  computers: "Компьютеры", computer: "Компьютер", tokens: "API-токены", settings: "Настройки организации",
  rename: "Название организации", invitation: "Отозвать приглашение", leave: "Выйти из организации", delete: "Удаление организации",
};

function DialogForm({ children, submit, onSubmit, onClose, disabled }: {
  children: ReactNode; submit: string; onSubmit: (event: FormEvent<HTMLFormElement>) => void; onClose: () => void; disabled?: boolean;
}) {
  return <form className="demo-form" onSubmit={onSubmit}>
    {children}
    <div className="dialog-actions"><Action onClick={onClose}>Отмена</Action><button className="work-action action-primary" disabled={disabled}>{submit}</button></div>
  </form>;
}

function RoleSelect({ role, change }: { role: InviteRole; change: (role: InviteRole) => void }) {
  return <label>Роль<select value={role} onChange={(event) => change(event.currentTarget.value as InviteRole)}>
    <option>Участник</option><option>Администратор</option>
  </select></label>;
}

function InviteForm({ update, close }: { update: Update; close: () => void }) {
  const { workspace } = useWorkspace();
  const [contact, setContact] = useState("");
  const [role, setRole] = useState<InviteRole>("Участник");
  const [mode, setMode] = useState<"invite" | "add" | "link">("invite");
  const [days, setDays] = useState("7");
  const [error, setError] = useState("");
  const submitLabel = { invite: "Отправить приглашение", add: "Добавить участника", link: "Создать ссылку" }[mode];

  return <DialogForm submit={submitLabel} onClose={close} disabled={mode !== "link" && !contact.trim()} onSubmit={(event) => {
    event.preventDefault();
    const value = contact.trim();
    const key = value.replace(/^@/, "").toLowerCase();
    if (mode !== "link" && !key) {
      setError("Введите почту или ник.");
      return;
    }
    if (mode !== "link" && workspace.members.some((item) => item.handle.toLowerCase() === key)) {
      setError("Этот пользователь уже в организации.");
      return;
    }
    if (mode === "invite" && workspace.invitations.some((item) => item.contact.toLowerCase() === value.toLowerCase())) {
      setError("Приглашение уже отправлено.");
      return;
    }
    const id = crypto.randomUUID();
    if (mode === "add") {
      const member: Member = { id, name: value.replace(/^@/, ""), handle: key, role, color: "violet" };
      update((current) => ({ ...current, members: [...current.members, member] }), "Участник добавлен в демоорганизацию.");
    } else {
      const invitation: Invitation = { id, contact: mode === "link" ? `Открытое приглашение · ${days} дн.` : value, role, sent: "Только что" };
      update((current) => ({ ...current, invitations: [...current.invitations, invitation] }), "Демоприглашение создано.");
    }
  }}>
    <label>Способ предоставления доступа<select value={mode} onChange={(event) => { setMode(event.currentTarget.value as typeof mode); setError(""); }}>
      <option value="invite">Именное приглашение</option><option value="add">Добавить напрямую</option><option value="link">Приглашение по ссылке</option>
    </select></label>
    {mode !== "link" && <label>Почта или ник<input required maxLength={100} autoComplete="off" placeholder="name@example.com" value={contact} onChange={(event) => { setContact(event.currentTarget.value); setError(""); }} /></label>}
    <RoleSelect role={role} change={setRole} />
    {mode !== "add" && <label>Срок действия<select value={days} onChange={(event) => setDays(event.currentTarget.value)}><option value="1">1 день</option><option value="7">7 дней</option><option value="30">30 дней</option></select></label>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </DialogForm>;
}

function EditMember({ member, update, close }: { member: Member; update: Update; close: () => void }) {
  const [role, setRole] = useState<InviteRole>(member.role === "Создатель" ? "Администратор" : member.role);
  const { open } = useWorkspace();
  if (member.role === "Создатель") return <>
    <p><strong>{member.name}</strong> · Создатель</p>
    <p>Создатель управляет участниками, ресурсами и настройками организации.</p>
    <Action onClick={() => open({ kind: "members" })}>К участникам</Action>
  </>;
  return <DialogForm submit="Сохранить" onClose={close} disabled={role === member.role} onSubmit={(event) => {
    event.preventDefault();
    update((current) => ({ ...current, members: current.members.map((item) => item.id === member.id ? { ...item, role } : item) }), "Роль участника обновлена.");
  }}><p><strong>{member.name}</strong> · @{member.handle}</p><RoleSelect role={role} change={setRole} /></DialogForm>;
}

function RenameForm({ update, close }: { update: Update; close: () => void }) {
  const { workspace } = useWorkspace();
  const [name, setName] = useState(workspace.name);
  return <DialogForm submit="Сохранить" onClose={close} disabled={!name.trim() || name.trim() === workspace.name} onSubmit={(event) => {
    event.preventDefault();
    const value = name.trim();
    if (value) update((current) => ({ ...current, name: value }), "Название сохранено.");
  }}><label>Название<input required maxLength={200} value={name} onChange={(event) => setName(event.currentTarget.value)} /></label></DialogForm>;
}

function DeletionPreview({ close }: { close: () => void }) {
  const { workspace, open } = useWorkspace();
  return <div className="deletion-preview">
    <p>Чтобы удалить «{workspace.name}», сначала освободите её ресурсы и отзовите доступ.</p>
    <div className="release-options">
      <Action icon="users" onClick={() => open({ kind: "members" })}>Участники · {workspace.members.length - 1}</Action>
      <Action icon="computer" onClick={() => open({ kind: "computers" })}>Компьютеры · {machines.length}</Action>
      <Action icon="key" onClick={() => open({ kind: "tokens" })}>API-токены</Action>
      {workspace.invitations.length > 0 && <Action onClick={() => open({ kind: "invites" })}>Приглашения · {workspace.invitations.length}</Action>}
    </div>
    <div className="dialog-actions"><Action onClick={close}>Готово</Action></div>
  </div>;
}

function DialogContent({ state, close, update }: { state: DialogState; close: () => void; update: Update }) {
  const { workspace, open } = useWorkspace();
  switch (state.kind) {
    case "invite": return <InviteForm update={update} close={close} />;
    case "invites": return <Invitations />;
    case "members": return <MemberList members={workspace.members} />;
    case "member": {
      const member = workspace.members.find((item) => item.id === state.id);
      return member && <EditMember member={member} update={update} close={close} />;
    }
    case "computers": return <ComputerList details />;
    case "computer": {
      const machine = machines.find((item) => item.id === state.id);
      return machine && <>
        <div className="computer-summary"><span className="machine-icon"><Icon name="computer" size={24} /></span><div><strong>{machine.name}</strong><small>{machine.platform}</small></div><Status online={machine.online} /></div>
        <dl className="computer-specs"><div><dt>Свободные ядра</dt><dd>{machine.cpu} / {machine.cores}</dd></div><div><dt>Свободная память</dt><dd>{machine.memory} / {machine.ram} ГиБ</dd></div></dl>
        <Action onClick={() => open({ kind: "computers" })}>К списку компьютеров</Action>
      </>;
    }
    case "tokens": return <TokenList />;
    case "settings": return <Settings />;
    case "rename": return <RenameForm update={update} close={close} />;
    case "invitation": {
      const invitation = workspace.invitations.find((item) => item.id === state.id);
      return invitation && <><p>Закрыть доступ по приглашению для <strong>{invitation.contact}</strong>?</p>
        <div className="dialog-actions"><Action onClick={close}>Отмена</Action><Action tone="danger" onClick={() => update((current) => ({ ...current, invitations: current.invitations.filter((item) => item.id !== invitation.id) }), "Приглашение отозвано.")}>Отозвать</Action></div>
      </>;
    }
    case "leave": return <><p>Сначала назначьте другого создателя организации.</p><Action onClick={() => open({ kind: "members" })}>К участникам</Action></>;
    case "delete": return <DeletionPreview close={close} />;
  }
}

export function WorkspaceDialog({ state, theme, opener, close, update }: {
  state: DialogState; theme: Theme; opener: HTMLButtonElement | null; close: () => void; update: Update;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const title = useId();
  const viewKey = "id" in state ? `${state.kind}:${state.id}` : state.kind;

  useLayoutEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => {
      dialog.close();
      opener?.focus({ preventScroll: true });
    };
  }, [opener]);

  useLayoutEffect(() => {
    heading.current?.focus({ preventScroll: true });
    ref.current?.scrollTo(0, 0);
  }, [viewKey]);

  return <dialog ref={ref} className="workspace-dialog" data-theme={theme} aria-labelledby={title} onCancel={(event) => { event.preventDefault(); close(); }}>
    <div className="dialog-title"><h2 id={title} ref={heading} tabIndex={-1}>{dialogTitles[state.kind]}</h2><Action icon="close" label="Закрыть окно" tone="quiet" onClick={close} /></div>
    <DialogContent key={viewKey} state={state} close={close} update={update} />
  </dialog>;
}
