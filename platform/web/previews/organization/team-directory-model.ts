import { type Member, type Workspace } from "./model";
import { teamDesigns, teamWorkspace, type TeamDesign } from "./team-model";

export const memberPreviewLimit = 5;
export const demoSizes = [4, 5, 6, 2000] as const;
export type DemoSize = (typeof demoSizes)[number];

const registry = teamDesigns[0];
export const memberLinkDesigns: readonly TeamDesign[] = [
  { ...registry, number: 81, name: "В шапке", description: "Текстовая ссылка рядом с названием виджета.", memberLink: "heading" },
  { ...registry, number: 82, name: "Рядом с вкладками", description: "Отдельное действие справа от вкладок.", memberLink: "tabs" },
  { ...registry, number: 83, name: "У поля поиска", description: "Кнопка в одной строке с поиском.", memberLink: "search" },
  { ...registry, number: 84, name: "Текст под списком", description: "Лёгкая ссылка по центру под последней строкой.", memberLink: "text" },
  { ...registry, number: 85, name: "Розовая кнопка", description: "Заметная кнопка во всю ширину под списком.", memberLink: "primary" },
  { ...registry, number: 86, name: "Счётчик внизу", description: "Сколько показано слева, переход справа.", memberLink: "footer" },
  { ...registry, number: 87, name: "Строка «Ещё»", description: "Следующая строка списка показывает, сколько участников осталось.", memberLink: "row" },
  { ...registry, number: 88, name: "Карточка перехода", description: "Ссылка с числом участников и крупной стрелкой в отдельной карточке.", memberLink: "card" },
  { ...registry, number: 89, name: "Сводка над строками", description: "Компактная строка с результатами и ссылкой между поиском и списком.", memberLink: "summary" },
  { ...registry, number: 90, name: "Боковая панель", description: "Переход со счётчиком справа от строк, на телефоне над списком.", memberLink: "rail" },
];

const firstNames = [
  ["София", "feminine"], ["Алексей", "masculine"], ["Ольга", "feminine"], ["Михаил", "masculine"], ["Нина", "feminine"],
  ["Илья", "masculine"], ["Лев", "masculine"], ["Мария", "feminine"], ["Егор", "masculine"], ["Вера", "feminine"],
] as const;
const surnames = [
  { feminine: "Орлова", masculine: "Орлов" }, { feminine: "Никитина", masculine: "Никитин" },
  { feminine: "Ким", masculine: "Ким" }, { feminine: "Демина", masculine: "Демин" },
  { feminine: "Фомина", masculine: "Фомин" }, { feminine: "Кравец", masculine: "Кравец" },
  { feminine: "Романова", masculine: "Романов" }, { feminine: "Миронова", masculine: "Миронов" },
  { feminine: "Ли", masculine: "Ли" }, { feminine: "Андреева", masculine: "Андреев" },
];
const handles = ["orlov", "nikitin", "kim", "demin", "fomin", "kravets", "romanov", "mironov", "lee", "andreev"];
const directoryMembers = [
  ...teamWorkspace.members,
  ...Array.from({ length: 2000 - teamWorkspace.members.length }, (_, index): Member => {
    const given = index % firstNames.length;
    const family = (Math.floor(index / firstNames.length) + given * 2) % surnames.length;
    const [name, form] = firstNames[given];
    const number = index + teamWorkspace.members.length + 1;
    return {
      id: `directory-${number}`,
      name: `${name} ${surnames[family][form]}`,
      handle: `${handles[family]}_${number}`,
      role: number % 29 === 0 ? "Администратор" : "Участник",
      color: number % 3 === 0 ? "mint" : number % 2 === 0 ? "amber" : "violet",
    };
  }),
];

export const directoryWorkspaces: Record<DemoSize, Workspace> = {
  4: teamWorkspace,
  5: { ...teamWorkspace, members: directoryMembers.slice(0, 5) },
  6: { ...teamWorkspace, members: directoryMembers.slice(0, 6) },
  2000: { ...teamWorkspace, members: directoryMembers },
};

const formatCount = new Intl.NumberFormat("ru-RU");
const plural = new Intl.PluralRules("ru-RU");
export const memberNumber = (value: number) => formatCount.format(value);
export function memberCount(value: number) {
  const word = { one: "участник", few: "участника", many: "участников", other: "участника", zero: "участников", two: "участника" }[plural.select(value)];
  return `${memberNumber(value)} ${word}`;
}

export function filterMembers(members: readonly Member[], search: string) {
  const needle = search.trim().replace(/^@/, "").toLocaleLowerCase("ru");
  return needle ? members.filter(({ name, handle }) => `${name} ${handle}`.toLocaleLowerCase("ru").includes(needle)) : members;
}

export function memberDirectoryHref(number: number, search = "") {
  const query = search.trim();
  return `#members-${number}${query ? `?${new URLSearchParams({ q: query })}` : ""}`;
}
