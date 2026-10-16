import { createThemeController, mountHeader } from "/src/index.ts";

const theme = createThemeController();
const header = document.querySelector(".tmc-header-shell");
let destroyHeader = mountHeader(header, { theme });
const label = document.querySelector('[data-testid="theme"]');
const update = (value) => { label.textContent = value; };
update(theme.getTheme());
theme.subscribe(update);
document.querySelector("#system-theme").addEventListener("click", () => theme.useSystemTheme());
document.querySelector("#remount").addEventListener("click", () => {
  destroyHeader();
  destroyHeader = mountHeader(header, { theme });
});
let count = 0;
document.querySelector("#action").addEventListener("click", () => {
  document.querySelector('[data-testid="count"]').textContent = String(++count);
});
