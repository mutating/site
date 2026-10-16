import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button, Header, ThemeProvider, ThemeToggle, useTheme } from "../src/react";
import "../src/styles/index.css";
import "./demo.css";

const language = { current: "ru", label: "Выбрать язык", groupLabel: "Языки", options: [{ code: "ru", href: "#ru" }, { code: "en", href: "#en" }] };
const items = [{ id: "components", label: "Компоненты", href: "#components" }, { id: "html", label: "HTML-версия", href: "/" }];
function Showcase() {
  const [mounted, setMounted] = useState(true);
  const [count, setCount] = useState(0);
  const [otherMenu, setOtherMenu] = useState(false);
  const { theme, controller } = useTheme();
  return <>
    {mounted && <Header
      brand={{ href: "/react.html" }} items={otherMenu ? [{ id: "overview", href: "#overview", label: "Обзор", active: true }, { id: "organizations", href: "#organizations", label: "Организации" }] : items} navigationLabel="Навигация"
      language={language} themeLabel="Переключить тему"
      menuLabel="Открыть меню" menuText="Меню"
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
          </div>
        </section>
      </div>
      <p className="demo-footer"><a className="tmc-text-link" href="/">Открыть HTML-версию →</a></p>
    </main>
  </>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><ThemeProvider><Showcase /></ThemeProvider></StrictMode>);
