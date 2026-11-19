export type Theme = "light" | "dark";

export const firstRound = [
  { number: "01", key: "cover", name: "Обложка", theme: "light", description: "Крупное название, три показателя и команда сразу под обложкой." },
  { number: "02", key: "laboratory", name: "Лаборатория", theme: "dark", description: "Обложка с Muta и компактное управление командой справа." },
  { number: "03", key: "dispatch", name: "Диспетчерская", theme: "dark", description: "Нагрузка, компьютеры и команда в поле зрения одновременно." },
  { number: "04", key: "ledger", name: "Картотека", theme: "light", description: "Строгие таблицы и быстрый переход к любому разделу." },
  { number: "05", key: "network", name: "Сеть", theme: "dark", description: "Состояние отдельных машин на схеме вычислительного пула." },
  { number: "06", key: "team", name: "Команда", theme: "light", description: "Карточки людей, их роли и быстрый доступ к приглашениям." },
  { number: "07", key: "capacity", name: "Мощности", theme: "light", description: "Доступные CPU и память на первом экране, история рядом." },
  { number: "08", key: "monolith", name: "Монолит", theme: "light", description: "Тёмная вертикаль с контекстом организации и рабочая колонка." },
  { number: "09", key: "journal", name: "Журнал", theme: "light", description: "Последние изменения в центре, участники и ресурсы рядом." },
  { number: "10", key: "terminal", name: "Консоль", theme: "dark", description: "Техническая сводка и плотные строки в духе панели Compute." },
  { number: "11", key: "pink", name: "Розовый блок", theme: "light", description: "Яркая обложка и рабочие панели, заходящие на неё снизу." },
  { number: "12", key: "grid", name: "Белая сетка", theme: "light", description: "Тонкие линии, крупные цифры и спокойный ритм таблиц." },
  { number: "13", key: "atlas", name: "Атлас", theme: "dark", description: "Компьютеры в карточках, сводка сети и статусы каждой машины." },
  { number: "14", key: "portal", name: "Портал", theme: "light", description: "Центральный знак организации и три входа в рабочие разделы." },
  { number: "15", key: "columns", name: "Две колонки", theme: "light", description: "Два равноправных раздела, команда слева и компьютеры справа." },
  { number: "16", key: "tabs", name: "Вкладки", theme: "dark", description: "Компактная шапка и отдельный экран для каждого раздела." },
  { number: "17", key: "bento", name: "Мозаика", theme: "light", description: "Панели разного размера объединяют людей, ресурсы и доступ." },
  { number: "18", key: "profile", name: "Профиль", theme: "light", description: "Паспорт организации слева, команда и приглашения справа." },
  { number: "19", key: "panorama", name: "Панорама", theme: "dark", description: "Широкая обложка с графикой сайта, цифры вынесены на светлую полосу." },
  { number: "20", key: "layers", name: "Слои", theme: "light", description: "Розовые плоскости, список участников и сводка ресурсов." },
] as const satisfies readonly { number: string; key: string; name: string; theme: Theme; description: string }[];

export const bases = ["13", "11", "19"] as const;

export const secondRound = [
  { number: "21", key: "atlas-focus", base: "13", name: "Пул в фокусе", theme: "dark", description: "Обложка и три компьютера собраны в одну панель." },
  { number: "22", key: "atlas-spread", base: "13", name: "Разворот", theme: "light", description: "Светлый рабочий экран и отдельная иллюстрация на всю высоту шапки." },
  { number: "23", key: "atlas-rail", base: "13", name: "Вертикальный атлас", theme: "dark", description: "Обложка слева, доступные машины и команда в правой колонке." },
  { number: "24", key: "atlas-display", base: "13", name: "Витрина", theme: "dark", description: "Широкая иллюстрация с карточками машин поверх нижнего края." },
  { number: "25", key: "atlas-console", base: "13", name: "Диспетчерский атлас", theme: "dark", description: "Компактная обложка и график CPU стоят рядом над списком машин." },
  { number: "26", key: "atlas-light", base: "13", name: "Светлый атлас", theme: "light", description: "Приглушённая графика, светлая сетка и мощности сразу под названием." },
  { number: "27", key: "atlas-devices", base: "13", name: "Карточки машин", theme: "dark", description: "По каждому компьютеру сразу видны статус, свободные ядра и память." },
  { number: "28", key: "atlas-nodes", base: "13", name: "Узлы", theme: "dark", description: "Компьютеры открываются прямо с иллюстрации вычислительного пула." },
  { number: "29", key: "atlas-pink", base: "13", name: "Малиновый атлас", theme: "light", description: "Розовый блок №11 и графика «Атласа» в одной обложке." },
  { number: "30", key: "atlas-tabs", base: "13", name: "Атлас с вкладками", theme: "dark", description: "Машины открыты первыми, команда и настройки доступны во вкладках." },
  { number: "31", key: "atlas-active", base: "13", name: "Активный пул", theme: "dark", description: "Самая свободная машина выделена, статусы остальных видны рядом." },
  { number: "32", key: "atlas-mural", base: "13", name: "Панно", theme: "light", description: "Полоса с графикой над строгой шапкой и таблицей ресурсов." },
  { number: "33", key: "pink-summary", base: "11", name: "Розовая сводка", theme: "light", description: "Название и основные показатели на одной яркой плоскости." },
  { number: "34", key: "pink-contrast", base: "11", name: "Контраст", theme: "dark", description: "Розовый блок выделяет свободные ресурсы в тёмной рабочей области." },
  { number: "35", key: "pink-profile", base: "11", name: "Розовый профиль", theme: "light", description: "Цветной профиль организации слева, команда и приглашения справа." },
  { number: "36", key: "pink-cascade", base: "11", name: "Каскад", theme: "light", description: "Белая рабочая область накладывается на обложку с монограммой." },
  { number: "37", key: "field-center", base: "19", name: "Поле", theme: "dark", description: "Графика на весь фон, название по центру и светлая полоса показателей." },
  { number: "38", key: "field-horizon", base: "19", name: "Горизонт", theme: "dark", description: "Низкая панорамная шапка оставляет на первом экране больше места данным." },
  { number: "39", key: "field-pulse", base: "19", name: "Пульс", theme: "dark", description: "Статусы машин и журнал событий видны сразу под панорамой." },
  { number: "40", key: "field-light", base: "19", name: "Светлая панорама", theme: "light", description: "Светлая рабочая область и отдельная рамка для панорамной графики." },
] as const satisfies readonly { number: string; key: string; base: (typeof bases)[number]; name: string; theme: Theme; description: string }[];

export const thirdRound = [
  { number: "41", key: "atlas-clear", base: "26", family: "light", theme: "light", name: "Чистый атлас", description: "Мягкое панно в шапке, единая сводка и две рабочие колонки." },
  { number: "42", key: "atlas-porcelain", base: "26", family: "light", theme: "light", name: "Фарфор", description: "Центральный заголовок, полупрозрачный рельеф и белые панели ресурсов." },
  { number: "43", key: "atlas-dashboard", base: "26", family: "light", theme: "light", name: "Светлый пульт", description: "Низкая шапка, свободные мощности и график нагрузки на первом экране." },
  { number: "44", key: "atlas-overlap", base: "26", family: "light", theme: "light", name: "Машины на обложке", description: "Карточки компьютеров заходят на нижний край светлой обложки." },
  { number: "45", key: "atlas-lines", base: "26", family: "light", theme: "light", name: "Линии", description: "Открытая сетка, крупные цифры и тонкая розовая линия по краю." },
  { number: "46", key: "atlas-index", base: "26", family: "light", theme: "light", name: "Светлый каталог", description: "Компактный рельеф, навигация по разделам и таблица машин." },
  { number: "47", key: "atlas-racks", base: "26", family: "light", theme: "light", name: "Машины в ряд", description: "Три широкие карточки показывают свободные ядра, память и статус." },
  { number: "48", key: "atlas-members", base: "26", family: "light", theme: "light", name: "Сводка команды", description: "Роли участников на первом экране, сводка ресурсов рядом." },
  { number: "49", key: "mural-wide", base: "32", family: "mural", theme: "light", name: "Широкий кадр", description: "Выразительный крупный план, название под ним и каталог машин." },
  { number: "50", key: "mural-strip", base: "32", family: "mural", theme: "light", name: "Узкая лента", description: "Низкое панно освобождает место для компьютеров и участников." },
  { number: "51", key: "mural-caption", base: "32", family: "mural", theme: "light", name: "Подпись на панно", description: "Белая плашка с названием перекрывает нижний край панно." },
  { number: "52", key: "mural-inset", base: "32", family: "mural", theme: "light", name: "Панно внутри", description: "Верхняя строка названия и действий, иллюстрация в рамке под ней." },
  { number: "53", key: "mural-tabs", base: "32", family: "mural", theme: "light", name: "Панно с вкладками", description: "Короткая шапка с панно. Компьютеры, команда и настройки во вкладках." },
  { number: "54", key: "mural-triptych", base: "32", family: "mural", theme: "light", name: "Триптих", description: "Три широких фрагмента одной иллюстрации над рабочей областью." },
  { number: "55", key: "mural-metrics", base: "32", family: "mural", theme: "light", name: "Сводка на панно", description: "Полоса показателей проходит вдоль нижнего края иллюстрации." },
  { number: "56", key: "mural-lower", base: "32", family: "mural", theme: "light", name: "Нижнее панно", description: "Машины и свободные мощности идут первыми, панно делит страницу ниже." },
  { number: "57", key: "pink-trim", base: "32", family: "accent", theme: "light", name: "Малиновая кромка", description: "Яркая полоса из №29 обрамляет панно и подчёркивает заголовок." },
  { number: "58", key: "pink-metrics", base: "26", family: "accent", theme: "light", name: "Розовые показатели", description: "Розовый из №29 выделяет сводку под спокойной светлой обложкой." },
  { number: "59", key: "pink-relief", base: "32", family: "accent", theme: "light", name: "Розовый рельеф", description: "Широкое панно на насыщенном розовом фоне и светлая рабочая область." },
  { number: "60", key: "pink-capacity", base: "26", family: "accent", theme: "light", name: "Розовые мощности", description: "Крупная яркая карточка CPU и памяти в светлой рабочей сетке." },
] as const satisfies readonly { number: string; key: string; base: "26" | "32"; family: "light" | "mural" | "accent"; theme: Theme; name: string; description: string }[];

export const rounds = [1, 2, 3] as const;
export type Round = (typeof rounds)[number];
export const designsByRound = { 1: firstRound, 2: secondRound, 3: thirdRound };
export const designs = [...firstRound, ...secondRound, ...thirdRound];
export type OriginalDesign = (typeof firstRound)[number];
export type RefinedDesign = (typeof secondRound)[number];
export type ThirdRoundDesign = (typeof thirdRound)[number];
export type Design = (typeof designs)[number];
export type Role = "Создатель" | "Администратор" | "Участник";

export interface Member {
  id: string;
  name: string;
  handle: string;
  role: Role;
  color: "pink" | "violet" | "mint" | "amber";
}

export interface Invitation {
  id: string;
  contact: string;
  role: Exclude<Role, "Создатель">;
  sent: string;
}

export const initialWorkspace = {
  name: "Северная лаборатория",
  members: [
    { id: "alisa", name: "Алиса Волкова", handle: "volkova", role: "Создатель", color: "pink" },
    { id: "boris", name: "Борис Левин", handle: "blevin", role: "Администратор", color: "violet" },
    { id: "maya", name: "Майя Соколова", handle: "maya", role: "Участник", color: "mint" },
    { id: "danil", name: "Данил Чен", handle: "dchen", role: "Участник", color: "amber" },
  ] as Member[],
  invitations: [
    { id: "anita", contact: "anita@example.com", role: "Участник", sent: "Сегодня, 10:42" },
    { id: "ilya", contact: "ilya@example.com", role: "Администратор", sent: "Вчера, 16:10" },
  ] as Invitation[],
};

export type Workspace = typeof initialWorkspace;

export const machines = [
  { id: "studio", name: "mac-studio", platform: "macOS · arm64", online: true, cpu: 8, cores: 12, memory: 24, ram: 32 },
  { id: "workstation", name: "workstation-01", platform: "Linux · x86_64", online: true, cpu: 6, cores: 8, memory: 14, ram: 32 },
  { id: "runner", name: "runner-03", platform: "Linux · x86_64", online: false, cpu: 0, cores: 12, memory: 0, ram: 16 },
] as const;

export type Machine = (typeof machines)[number];

export const tokens = [
  { name: "CI · основная ветка", scope: "Чтение и запуск задач", expiry: "18 дек. 2026" },
  { name: "Мониторинг", scope: "Только чтение", expiry: "Без срока" },
];

export const activity = [
  { time: "10:42", date: "Сегодня", title: "Отправлено приглашение", detail: "Алиса Волкова · anita@example.com", icon: "users" },
  { time: "09:18", date: "Сегодня", title: "mac-studio подключён", detail: "macOS · в пуле организации", icon: "computer" },
  { time: "16:10", date: "Вчера", title: "Отправлено приглашение", detail: "Борис Левин · ilya@example.com", icon: "users" },
  { time: "14:32", date: "Вчера", title: "Обновлён доступ к ресурсам", detail: "Борис Левин · настройки команды", icon: "lock" },
] as const;

export const stats = {
  online: machines.filter((machine) => machine.online).length,
  freeCpu: machines.reduce((sum, machine) => sum + machine.cpu, 0),
  totalCpu: machines.reduce((sum, machine) => sum + machine.cores, 0),
  freeMemory: machines.reduce((sum, machine) => sum + machine.memory, 0),
  totalMemory: machines.reduce((sum, machine) => sum + machine.ram, 0),
};

export function initials(name: string) {
  return name.trim().split(/\s+/).slice(0, 2).map((part) => Array.from(part)[0]).join("").toLocaleUpperCase("ru");
}
