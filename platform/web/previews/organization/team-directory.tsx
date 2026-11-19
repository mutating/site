import { useRef, useState } from "react";
import { Icon } from "../../src/components/UI";
import { useWorkspace } from "./parts";
import { filterMembers, memberCount, memberNumber } from "./team-directory-model";
import { MemberRows, MemberSearch } from "./team-widget";

const pageSize = 20;
type DirectoryState = { search: string; page: number };

export function MemberDirectory({ number, initialSearch }: { number: number; initialSearch: string }) {
  const { workspace } = useWorkspace();
  const [{ search, page }, setDirectoryState] = useState<DirectoryState>(() => history.state?.memberDirectory ?? { search: initialSearch, page: 1 });
  const range = useRef<HTMLParagraphElement>(null);
  const members = filterMembers(workspace.members, search);
  const pages = Math.max(1, Math.ceil(members.length / pageSize));
  const currentPage = Math.min(page, pages);
  const offset = (currentPage - 1) * pageSize;
  const entries = members.slice(offset, offset + pageSize);
  const start = memberNumber(offset + 1);
  const end = entries.length === 1 ? "" : `–${memberNumber(offset + entries.length)}`;
  const rangeLabel = entries.length ? `${start}${end} из ${memberNumber(members.length)}` : "Участники не найдены";

  function show(next: DirectoryState) {
    history.replaceState({ ...history.state, memberDirectory: next }, "");
    setDirectoryState(next);
  }

  function goToPage(next: number) {
    show({ search, page: next });
    requestAnimationFrame(() => {
      range.current?.focus({ preventScroll: true });
      range.current?.scrollIntoView({ block: "start" });
    });
  }

  return <div className="member-directory-page">
    <a href={`#${number}`} className="member-directory-back"><Icon name="arrow" size={16} />К организации</a>
    <header className="member-directory-heading">
      <div><p className="eyebrow">{workspace.name}</p><h1 tabIndex={-1}>Участники<span className="hot" aria-hidden="true">.</span></h1></div>
      <span><Icon name="users" size={18} />{memberCount(workspace.members.length)}</span>
    </header>
    <section className="organization-widget team-widget member-directory-list" data-variant="61" data-roster="rows" aria-label="Полный список участников">
      <div className="member-directory-tools">
        <MemberSearch value={search} setValue={(value) => show({ search: value, page: 1 })} />
        <p ref={range} className="member-directory-range" role="status" tabIndex={-1}>{rangeLabel}</p>
      </div>
      {entries.length ? <MemberRows members={entries} /> : <p className="team-empty">Попробуйте другое имя или ник.</p>}
      {pages > 1 && <nav className="member-directory-pagination" aria-label="Страницы списка участников">
        <button type="button" className="button secondary compact" disabled={currentPage === 1} onClick={() => goToPage(currentPage - 1)}>← Назад</button>
        <label><span>Страница</span>
          <select value={currentPage} aria-label="Страница списка" onChange={(event) => goToPage(Number(event.currentTarget.value))}>
            {Array.from({ length: pages }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}
          </select>
          <span>из {pages}</span>
        </label>
        <button type="button" className="button secondary compact" disabled={currentPage === pages} onClick={() => goToPage(currentPage + 1)}>Далее →</button>
      </nav>}
    </section>
  </div>;
}
