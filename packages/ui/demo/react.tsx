import { StrictMode, useState, type ComponentPropsWithoutRef } from "react";
import { createRoot } from "react-dom/client";
import { Button, Header, ThemeProvider, ThemeToggle, useTheme } from "../src/react";
import "../src/styles/index.css";
import "./demo.css";

const language = { current: "ru", label: "Выбрать язык", groupLabel: "Языки", options: [{ code: "ru", href: "#ru" }, { code: "en", href: "#en" }] };
const items = [{ id: "components", label: "Компоненты", href: "#components" }, { id: "html", label: "HTML-версия", href: "/" }];
const brandQuery = new URLSearchParams(location.search);
function DemoRouterLink(props: ComponentPropsWithoutRef<"a">) {
  return <a {...props} data-demo-router-link="" />;
}
function Showcase() {
  const [mounted, setMounted] = useState(true);
  const [count, setCount] = useState(0);
  const [otherMenu, setOtherMenu] = useState(false);
  const [extraLanguage, setExtraLanguage] = useState(false);
  const [currentLanguage, setCurrentLanguage] = useState("ru");
  const [wrappedLinks, setWrappedLinks] = useState(false);
  const { theme, controller } = useTheme();
  return <>
    {mounted && <Header
      brand={{ href: brandQuery.get("brand-home") ?? "/react.html", animation: brandQuery.has("reload-brand") ? { activation: "reload-home" } : undefined }} items={otherMenu ? [{ id: "overview", href: "#overview", label: "Обзор", active: true }, { id: "organizations", href: "#organizations", label: "Организации" }] : items} navigationLabel="Навигация"
      language={{ ...language, current: currentLanguage, options: extraLanguage ? [...language.options, { code: "de", href: "#de" }] : language.options }} themeLabel="Переключить тему"
      menuLabel="Открыть меню" menuText="Меню"
      renderLink={wrappedLinks ? (_item, attributes, children) => <DemoRouterLink {...attributes}>{children}</DemoRouterLink> : undefined}
    />}
    <main className="tmc-shell demo-content" id="components">
      <p className="tmc-eyebrow">@mutating/ui · React</p>
      <h1>Общий визуальный язык<span>.</span></h1>
      <p>Компоненты используют общие стили и поведение. Текущая тема: <strong data-testid="theme">{theme}</strong>.</p>
      <div className="demo-grid">
        <section className="tmc-surface demo-panel"><h2>Кнопки</h2><div className="demo-row">
          <Button onClick={() => setCount((value) => value + 1)}>Действие →</Button>
          <Button variant="secondary">Вторичная</Button>
          <Button variant="dark" size="compact">Компактная</Button>
          <Button disabled>Недоступна</Button>
        </div><p>Выполнено действий: <span data-testid="count">{count}</span></p></section>
        <section className="tmc-surface tmc-surface--raised demo-panel"><h2>Тема и жизненный цикл</h2>
          <div className="demo-row"><ThemeToggle aria-label="Второй переключатель темы" />
            <Button variant="secondary" size="compact" onClick={() => controller?.useSystemTheme()}>Системная тема</Button>
            <Button variant="secondary" size="compact" onClick={() => setMounted((value) => !value)}>Пересоздать хэдер</Button>
            <Button variant="secondary" size="compact" onClick={() => setOtherMenu((value) => !value)}>Другое меню</Button>
            <Button variant="secondary" size="compact" onClick={() => setExtraLanguage((value) => !value)}>Другие языки</Button>
            <Button variant="secondary" size="compact" onClick={() => setCurrentLanguage((value) => value === "ru" ? "en" : "ru")}>Сменить текущий язык</Button>
            <Button variant="secondary" size="compact" onClick={() => setWrappedLinks((value) => !value)}>Другие ссылки</Button>
          </div>
        </section>
      </div>
      <p className="demo-footer"><a className="tmc-text-link" href="/">Открыть HTML-версию →</a></p>
    </main>
  </>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><ThemeProvider><Showcase /></ThemeProvider></StrictMode>);
