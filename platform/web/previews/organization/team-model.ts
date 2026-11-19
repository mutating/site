import { initialWorkspace, type Workspace } from "./model";

type Navigation = "top" | "header" | "rail" | "bottom" | "disclosure" | "parallel" | "drawer";
type Roster = "rows" | "cards" | "portraits" | "avatars" | "master" | "profiles" | "groups" | "leader" | "columns" | "table";
export type MemberLinkStyle = "heading" | "tabs" | "search" | "text" | "primary" | "footer" | "row" | "card" | "summary" | "rail";

export type TeamDesign = {
  number: number;
  name: string;
  description: string;
  navigation: Navigation;
  roster: Roster;
  memberLink?: MemberLinkStyle;
};

export const teamDesigns: readonly TeamDesign[] = [
  { number: 61, name: "Реестр", description: "Поиск по имени и нику, тонкие разделители и роли справа.", navigation: "top", roster: "rows" },
  { number: 62, name: "Картотека", description: "Отдельная карточка на участника, роль под именем.", navigation: "top", roster: "cards" },
  { number: 63, name: "Боковые вкладки", description: "Вертикальный переключатель слева от списка.", navigation: "rail", roster: "rows" },
  { number: 64, name: "Цветовые метки", description: "Метка роли на левом краю каждой строки, имя на первом плане.", navigation: "header", roster: "rows" },
  { number: 65, name: "Портреты", description: "Две колонки с крупными аватарами и ролями под именами.", navigation: "top", roster: "portraits" },
  { number: 66, name: "Лента аватаров", description: "Выбор участника по аватару, права выбранного сразу под лентой.", navigation: "header", roster: "avatars" },
  { number: 67, name: "Карточка участника", description: "Список имён слева, профиль и права выбранного участника справа.", navigation: "top", roster: "master" },
  { number: 68, name: "Галерея профилей", description: "Сетка компактных профилей с ролью внизу каждой карточки.", navigation: "top", roster: "profiles" },
  { number: 69, name: "По ролям", description: "Участники сгруппированы по ролям с отдельными заголовками.", navigation: "top", roster: "groups" },
  { number: 70, name: "Создатель сверху", description: "Крупная карточка создателя над компактным списком остальных.", navigation: "header", roster: "leader" },
  { number: 71, name: "Две группы", description: "Управление и участники в соседних колонках.", navigation: "top", roster: "columns" },
  { number: 72, name: "Имя и роль", description: "Табличная верстка с отдельными колонками для имени и роли.", navigation: "top", roster: "table" },
  { number: 73, name: "Розовый контур", description: "Яркая линия вокруг светлой панели и розовая активная вкладка.", navigation: "top", roster: "rows" },
  { number: 74, name: "Розовая шапка", description: "Насыщенная розовая шапка с переключателем и спокойные карточки под ней.", navigation: "top", roster: "cards" },
  { number: 75, name: "Светлый рельеф", description: "Мягкие слои розового, выступающие строки и вертикальные вкладки.", navigation: "header", roster: "cards" },
  { number: 76, name: "Графит", description: "Тёмная панель в обеих темах, розовый акцент и компактные строки.", navigation: "top", roster: "rows" },
  { number: 77, name: "Две секции", description: "Участники и приглашения раскрываются независимо в одном виджете.", navigation: "disclosure", roster: "rows" },
  { number: 78, name: "Нижние вкладки", description: "Сначала карточки участников, переключатель закреплён внизу виджета.", navigation: "bottom", roster: "cards" },
  { number: 79, name: "Рядом", description: "Участники и приглашения видны одновременно, каждый список в своей колонке.", navigation: "parallel", roster: "rows" },
  { number: 80, name: "Панель приглашений", description: "Участники всегда видны. Приглашения открываются компактной панелью из шапки.", navigation: "drawer", roster: "rows" },
];

export const teamFamilies = ["Списки", "Профили", "Роли", "Цвет и фактура", "Навигация"].map((name, index) => {
  const offset = index * 4;
  return { name, first: teamDesigns[offset].number, entries: teamDesigns.slice(offset, offset + 4) };
});

export const teamWorkspace: Workspace = {
  ...initialWorkspace,
  invitations: [
    ...initialWorkspace.invitations,
    { id: "open-link", contact: "Ссылка без адресата", role: "Участник", sent: "Действует до 26 ноября" },
  ],
};
