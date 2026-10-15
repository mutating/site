import { createContext, useContext, useState, type ReactNode } from "react";

const messages = {
  personalType: ["Личный пул", "Personal"],
  organizationType: ["Организация", "Organization"],
  powerTag: ["Распределённые вычисления", "Distributed compute"],
  cloudTag: ["Облачные раннеры", "Cloud runners"],
  account: ["Личный кабинет", "Account"],
  home: ["Обзор", "Overview"],
  cloud: ["Облако", "Cloud"],
  organizations: ["Организации", "Organizations"],
  profile: ["Профиль", "Profile"],
  logout: ["Выйти", "Sign out"],
  login: ["Войти", "Sign in"],
  register: ["Создать аккаунт", "Create account"],
  welcome: ["С возвращением", "Welcome back"],
  loginLead: [
    "Войдите, чтобы управлять своими мощностями.",
    "Sign in to manage your computing resources.",
  ],
  registerTitle: ["Начнём знакомство", "Let’s get started"],
  registerLead: [
    "Один аккаунт для всей вашей инфраструктуры.",
    "One account for all your infrastructure.",
  ],
  identifier: ["Email или ник", "Email or nickname"],
  email: ["Электронная почта", "Email address"],
  password: ["Пароль", "Password"],
  firstName: ["Имя", "First name"],
  lastName: ["Фамилия", "Last name"],
  nickname: ["Ник", "Nickname"],
  optional: ["необязательно", "optional"],
  passwordHelp: [
    "Не менее 12 символов. Пробелы тоже считаются.",
    "At least 12 characters. Spaces count, too.",
  ],
  nicknameHelp: [
    "Латинские буквы, цифры, точка, дефис или подчёркивание.",
    "Letters, numbers, periods, hyphens or underscores.",
  ],
  newUser: ["Ещё нет аккаунта?", "New here?"],
  existingUser: ["Уже есть аккаунт?", "Already have an account?"],
  showPassword: ["Показать пароль", "Show password"],
  hidePassword: ["Скрыть пароль", "Hide password"],
  waiting: ["Подождите…", "Please wait…"],
  hello: ["Привет", "Hello"],
  helloFallback: ["Привет!", "Hello!"],
  overviewLead: [
    "Ваша инфраструктура. Всё под рукой.",
    "Your infrastructure. All in one place.",
  ],
  productLabel: ["Ваши продукты", "Your products"],
  personal: ["Личные компьютеры", "Personal computers"],
  personalLead: [
    "Мощности, которые принадлежат вам.",
    "Computing resources owned by you.",
  ],
  powerLead: [
    "Ваши компьютеры работают вместе.",
    "Your computers, working together.",
  ],
  powerDescription: [
    "Распределённая сеть компьютеров для выполнения задач через Throng.",
    "A distributed fleet of computers for running tasks with Throng.",
  ],
  organizationLead: [
    "Вы состоите в этих организациях. Их мощности доступны для общих задач.",
    "You belong to these organizations. Their resources are available for shared tasks.",
  ],
  noOrganizations: [
    "Вы пока не состоите в организациях.",
    "You haven’t joined any organizations yet.",
  ],
  organizationsLead: [
    "Общие мощности начинаются с команды.",
    "Shared resources start with a team.",
  ],
  createOrganization: ["Создать организацию", "Create organization"],
  organizationName: ["Название организации", "Organization name"],
  createdOrganizations: ["Созданные вами", "Created by you"],
  joinedOrganizations: ["Ваши организации", "Your organizations"],
  noCreatedOrganizations: [
    "Вы ещё не создали ни одной организации.",
    "You haven’t created any organizations yet.",
  ],
  creator: ["Создатель", "Creator"],
  admin: ["Администратор", "Administrator"],
  user: ["Участник", "Member"],
  leave: ["Выйти из организации", "Leave organization"],
  deleteOrganization: ["Удалить организацию", "Delete organization"],
  manageOrganizations: ["Управлять организациями", "Manage organizations"],
  members: ["Участники", "Members"],
  membersLead: [
    "Люди, доступ и общая работа.",
    "People, access, and shared work.",
  ],
  backOrganizations: ["К организациям", "Back to organizations"],
  memberRole: ["Роль", "Role"],
  memberPermissions: ["Права", "Permissions"],
  customPermissions: ["Настроить права отдельно", "Customize permissions"],
  roleDefaults: ["Права по роли", "Role defaults"],
  creatorPermissions: [
    "Создатель сохраняет полный доступ. В организации должен оставаться хотя бы один создатель.",
    "Creators retain full access. The organization must keep at least one creator.",
  ],
  permissionMembersRead: ["Просмотр участников", "View members"],
  permissionMembersManage: [
    "Управление участниками и приглашениями",
    "Manage members and invitations",
  ],
  permissionResourcesRead: [
    "Просмотр ресурсов и истории мощностей",
    "View resources and capacity history",
  ],
  permissionResourcesManage: [
    "Управление компьютерами и ресурсами",
    "Manage computers and resources",
  ],
  permissionExecutionRead: [
    "Просмотр задач, результатов и артефактов",
    "View tasks, results, and artifacts",
  ],
  permissionExecutionRun: [
    "Выполнение задач и работа с артефактами",
    "Run tasks and manage artifacts",
  ],
  permissionTokensRead: ["Просмотр API-токенов", "View API tokens"],
  permissionTokensManage: ["Управление API-токенами", "Manage API tokens"],
  permissionCompanyManage: ["Настройки организации", "Organization settings"],
  editMember: ["Изменить права", "Edit permissions"],
  removeMember: ["Удалить участника", "Remove member"],
  removeMemberWarning: [
    "Участник потеряет доступ к организации. Его персональные данные и ресурсы сохранятся.",
    "The member will lose access to the organization. Their personal data and resources will remain.",
  ],
  memberConflict: [
    "Участник уже добавлен или изменение его прав сейчас невозможно.",
    "The member already exists or their permissions cannot be changed right now.",
  ],
  noPermissions: [
    "Дополнительные права не предоставлены.",
    "No additional permissions granted.",
  ],
  memberReadDenied: [
    "У вас нет права просматривать участников этой организации.",
    "You do not have permission to view this organization’s members.",
  ],
  resourcesReadDenied: [
    "У вас нет права просматривать мощности этой организации.",
    "You do not have permission to view this organization’s capacity.",
  ],
  addMember: ["Добавить участника", "Add member"],
  sharingMethod: ["Способ добавления", "Admission method"],
  directMember: [
    "Добавить зарегистрированного пользователя",
    "Add a registered user",
  ],
  addressedInvite: [
    "Пригласить по email или нику",
    "Invite by email or nickname",
  ],
  openInvite: [
    "Создать ссылку без адресата",
    "Create a link without a recipient",
  ],
  findUser: ["Найти пользователя", "Find user"],
  userNotFound: [
    "Пользователь не найден. Проверьте email или ник; нового пользователя можно пригласить по email.",
    "User not found. Check the email or nickname; a new user can be invited by email.",
  ],
  openInviteHelp: [
    "Любой пользователь с этой одноразовой ссылкой сможет принять приглашение. Передавайте её выбранному получателю.",
    "Any user with this single-use link can accept the invitation. Share it with your chosen recipient.",
  ],
  inviteMember: ["Пригласить", "Invite"],
  createInviteLink: ["Создать ссылку", "Create link"],
  invitationDays: ["Срок приглашения", "Invitation lifetime"],
  oneDay: ["1 день", "1 day"],
  sevenDaysInvite: ["7 дней", "7 days"],
  thirtyDays: ["30 дней", "30 days"],
  memberAdded: ["Участник добавлен.", "Member added."],
  invitationCreated: ["Приглашение создано.", "Invitation created."],
  invitations: ["Приглашения", "Invitations"],
  incomingInvitations: ["Вас пригласили", "You’re invited"],
  noInvitations: ["Приглашений пока нет.", "No invitations yet."],
  acceptInvitation: ["Принять приглашение", "Accept invitation"],
  declineInvitation: ["Отклонить", "Decline"],
  invitationAccepted: [
    "Вы присоединились к организации.",
    "You joined the organization.",
  ],
  invitationDeclined: ["Приглашение отклонено.", "Invitation declined."],
  invitationUnavailable: [
    "Приглашение уже использовано, отозвано или срок его действия закончился.",
    "This invitation has been used, revoked, or expired.",
  ],
  invitationWrongAccount: [
    "Это приглашение адресовано другой учётной записи. Войдите с аккаунтом получателя.",
    "This invitation belongs to another account. Sign in with the recipient’s account.",
  ],
  invitationConflict: [
    "Пользователь уже состоит в организации или для него уже есть действующее приглашение.",
    "The user is already a member or already has an active invitation.",
  ],
  revokeInvitation: ["Отозвать", "Revoke"],
  renewInvitationLink: ["Обновить ссылку", "Renew link"],
  renewLinkHelp: [
    "Обновление ссылки отключает предыдущую.",
    "Renewing a link disables the previous one.",
  ],
  invitationLink: ["Ссылка приглашения", "Invitation link"],
  copyLink: ["Скопировать ссылку", "Copy link"],
  copiedLink: ["Ссылка скопирована.", "Link copied."],
  selectLink: ["Ссылка выделена — скопируйте её.", "Link selected — copy it."],
  expiresOn: ["Действует до", "Expires on"],
  invitationPending: ["Ожидает ответа", "Pending"],
  invitationStatusAccepted: ["Принято", "Accepted"],
  invitationStatusDeclined: ["Отклонено", "Declined"],
  invitationStatusRevoked: ["Отозвано", "Revoked"],
  invitationStatusExpired: ["Срок истёк", "Expired"],
  organizationSettings: ["Настройки организации", "Organization settings"],
  selfPermissionChange: [
    "Ваши новые права начнут действовать сразу после сохранения.",
    "Your new permissions take effect immediately after saving.",
  ],
  total: ["всего", "total"],
  free: ["свободно", "free"],
  cores: ["ядер", "cores"],
  memory: ["Память", "Memory"],
  connected: ["Компьютеров онлайн", "Computers online"],
  available: ["Доступные мощности", "Available capacity"],
  viewComputers: ["Список компьютеров", "View computers"],
  onlineOnly: ["Только доступные сейчас", "Available right now"],
  resourcesNote: [
    "Учитываются включённые компьютеры онлайн. «Свободно» — за вычетом резервов под задачи.",
    "Includes enabled computers that are online. Free capacity excludes task reservations.",
  ],
  history: ["Мощности и загрузка", "Capacity & utilization"],
  sevenDays: ["Последние 7 дней", "Last 7 days"],
  cpuCapacity: ["CPU · мощность", "CPU · capacity"],
  memoryCapacity: ["Память · мощность", "Memory · capacity"],
  cpuUsage: ["CPU · занято", "CPU · reserved"],
  memoryUsage: ["Память · занято", "Memory · reserved"],
  chartNote: [
    "Сплошные линии — выделенная мощность; пунктир — занято задачами.",
    "Solid lines show allocated capacity; dashed lines show task reservations.",
  ],
  chartTable: ["Данные графика", "Chart data"],
  time: ["Время", "Time"],
  noMeasurements: [
    "Измерений загрузки пока нет.",
    "No utilization measurements yet.",
  ],
  historyFrom: ["История доступна с", "History is available from"],
  noHistory: ["История ещё не накоплена.", "No history has been recorded yet."],
  noComputers: [
    "Пока нет подключённых компьютеров",
    "No computers connected yet",
  ],
  noComputersLead: [
    "Когда ваши компьютеры подключатся через Throng, здесь появятся их мощности.",
    "When your computers connect through Throng, their resources will appear here.",
  ],
  computers: ["Компьютеры", "Computers"],
  computer: ["Компьютер", "Computer"],
  computerDetails: ["О компьютере", "Computer details"],
  computerId: ["ID компьютера", "Computer ID"],
  backComputers: ["К списку компьютеров", "Back to computers"],
  benchmarks: ["Бенчмарки", "Benchmarks"],
  latestBenchmarks: ["Последние бенчмарки", "Latest benchmarks"],
  latestBenchmarksLead: [
    "Результаты последней версии тулинга. Выберите бенчмарк, чтобы посмотреть историю.",
    "Results from the latest tooling version. Select a benchmark to view its history.",
  ],
  benchmarkVersion: ["Версия тулинга", "Tooling version"],
  benchmarkScore: ["Результат", "Score"],
  benchmarkHistory: ["История бенчмарка", "Benchmark history"],
  benchmarkHistoryOrder: [
    "По версии тулинга: от новых к старым.",
    "By tooling version, newest first.",
  ],
  noBenchmarks: ["Результатов пока нет", "No results yet"],
  noBenchmarksLead: [
    "Результаты появятся после запуска бенчмарков на этом компьютере.",
    "Results will appear after benchmarks run on this computer.",
  ],
  noBenchmarkHistory: [
    "Для этого бенчмарка результатов пока нет.",
    "No results for this benchmark yet.",
  ],
  moreBenchmarks: ["ещё", "more"],
  allBenchmarks: ["Все бенчмарки", "All benchmarks"],
  chooseBenchmark: ["Выберите бенчмарк", "Choose a benchmark"],
  status: ["Статус", "Status"],
  online: ["Онлайн", "Online"],
  offline: ["Офлайн", "Offline"],
  disabled: ["Отключён", "Disabled"],
  previous: ["Назад", "Previous"],
  next: ["Далее", "Next"],
  page: ["Страница", "Page"],
  of: ["из", "of"],
  backPower: ["К Power Cells", "Back to Power Cells"],
  refresh: ["Обновить", "Refresh"],
  cloudTitle: ["Облако — впереди", "Cloud is on the horizon"],
  cloudLead: [
    "Здесь появятся облачные раннеры. Пока этот продукт в разработке.",
    "Cloud runners will live here. This product is still in development.",
  ],
  soon: ["Скоро", "Coming soon"],
  profileLead: ["Немного о вас.", "A little about you."],
  profileAvatar: ["Аватар", "Avatar"],
  profileAvatarLead: ["Фото вашего профиля.", "Your profile photo."],
  profileDetails: ["Личные данные", "Personal details"],
  profileDetailsLead: ["Имя и ник в сервисе.", "Your name and nickname."],
  profileEmail: ["Почта", "Email"],
  profileEmailLead: ["Используется для входа.", "Used to sign in."],
  accountManagement: ["Управление аккаунтом", "Account management"],
  save: ["Сохранить изменения", "Save changes"],
  saved: ["Изменения сохранены.", "Changes saved."],
  uploadAvatar: ["Загрузить аватар", "Upload avatar"],
  removeAvatar: ["Удалить аватар", "Remove avatar"],
  avatarHelp: [
    "PNG, JPEG или WebP, до 2 МиБ.",
    "PNG, JPEG or WebP, up to 2 MiB.",
  ],
  avatarSize: [
    "Изображение должно быть не больше 2 МиБ.",
    "The image must not exceed 2 MiB.",
  ],
  accountDeletion: ["Удаление аккаунта", "Delete account"],
  deleteAccount: ["Удалить аккаунт", "Delete account"],
  deletionLead: [
    "Это действие нельзя отменить. Сначала нужно выйти из организаций и освободить связанные ресурсы.",
    "This cannot be undone. First leave your organizations and resolve any linked resources.",
  ],
  confirmDelete: ["Подтвердите удаление", "Confirm deletion"],
  confirmLeave: ["Подтвердите выход", "Confirm leaving"],
  typeToConfirm: ["Для подтверждения введите", "To confirm, enter"],
  irreversible: [
    "Это действие нельзя отменить.",
    "This action cannot be undone.",
  ],
  leaveWarning: [
    "Вы потеряете доступ к ресурсам организации.",
    "You will lose access to the organization’s resources.",
  ],
  cancel: ["Отмена", "Cancel"],
  confirm: ["Подтвердить", "Confirm"],
  invalidCredentials: [
    "Неверный логин или пароль.",
    "Incorrect login or password.",
  ],
  conflict: [
    "Email или ник уже используется.",
    "This email or nickname is already in use.",
  ],
  blockedDelete: [
    "Удаление заблокировано связанными ресурсами или историей выполнения. Сначала освободите зависимости.",
    "Deletion is blocked by linked resources or execution history. Resolve those dependencies first.",
  ],
  leaveBeforeDelete: [
    "Удаление заблокировано: сначала выйдите из организаций или удалите созданные вами организации.",
    "Deletion is blocked: leave your organizations or delete those you created first.",
  ],
  companyMembersBlockDelete: [
    "Удаление заблокировано: в организации есть другие участники. Сначала они должны выйти из неё.",
    "Deletion is blocked: other members must leave the organization first.",
  ],
  companyTokensBlockDelete: [
    "Удаление заблокировано: у организации есть API-токены или связи владения ими. Удалите их через API перед удалением организации.",
    "Deletion is blocked: the organization has API tokens or token ownerships. Remove them through the API before deleting the organization.",
  ],
  leaveBlocked: [
    "Организация должна сохранить хотя бы одного создателя. Передайте права или удалите организацию.",
    "The organization must retain at least one creator. Transfer ownership or delete the organization.",
  ],
  registrationDisabled: [
    "Регистрация временно недоступна.",
    "Registration is currently unavailable.",
  ],
  accountCreatedSignIn: [
    "Аккаунт создан. Автоматический вход не удался — войдите с вашим email и паролем.",
    "Your account has been created. Automatic sign-in failed — sign in with your email and password.",
  ],
  forbidden: [
    "У вас нет прав на это действие.",
    "You don’t have permission to do this.",
  ],
  invalidInput: [
    "Проверьте введённые данные.",
    "Please check the information you entered.",
  ],
  unavailable: [
    "Не удалось связаться с сервером. Попробуйте ещё раз.",
    "Couldn’t connect to the server. Please try again.",
  ],
  notFound: ["Страница не найдена", "Page not found"],
  notFoundLead: ["Возможно, адрес изменился.", "The address may have changed."],
  retry: ["Попробовать снова", "Try again"],
  loading: ["Загрузка…", "Loading…"],
  skip: ["Перейти к содержимому", "Skip to content"],
  theme: ["Переключить тему", "Toggle color theme"],
  language: ["Выбрать язык", "Choose language"],
  navigation: ["Навигация кабинета", "Account navigation"],
  menu: ["Меню", "Menu"],
  website: ["На сайт", "Visit website"],
  authSignal: [
    "Инфраструктура для ваших задач",
    "Infrastructure for your work",
  ],
  authHeading: [
    "Ваша сеть.\nВаши возможности.",
    "Your network.\nYour possibilities.",
  ],
  authDescription: [
    "Объединяйте компьютеры, распределяйте работу и создавайте вместе.",
    "Connect computers, distribute work and build together.",
  ],
  organizationCreated: ["Организация создана.", "Organization created."],
  sessionExpired: ["Войдите, чтобы продолжить.", "Sign in to continue."],
} as const;
export type MessageKey = keyof typeof messages;
export type Language = "ru" | "en";
type I18n = {
  language: Language;
  setLanguage: (l: Language) => void;
  t: (key: MessageKey) => string;
  locale: string;
};
const Context = createContext<I18n>(null!);
export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, update] = useState<Language>(
    document.documentElement.lang === "ru" ? "ru" : "en",
  );
  const setLanguage = (value: Language) => {
    update(value);
    document.documentElement.lang = value;
    try {
      localStorage.setItem("tmc-language", value);
    } catch {}
  };
  return (
    <Context.Provider
      value={{
        language,
        setLanguage,
        locale: language === "ru" ? "ru-RU" : "en-US",
        t: (key) => messages[key][language === "ru" ? 0 : 1],
      }}
    >
      {children}
    </Context.Provider>
  );
}
export const useI18n = () => useContext(Context);
