---
title: "Представляем getsources: получение исходного кода Python-функций"
description: "Получает чистый и пригодный для парсинга исходный код из объектов Python-функций, включая вложенные и интерактивные определения."
---

Инструментам преобразования и интроспекции нужен исходный текст для разбора Python-функции. Интерактивное определение может оказаться недоступным, а вложенная функция сохраняет внешний отступ и способна сломать следующий этап парсинга.

Стандартное решение — [`inspect.getsource()`](https://docs.python.org/3/library/inspect.html#inspect.getsource), а [dill](https://dill.readthedocs.io/en/latest/dill.html#module-dill.source) и [sourceinspect](https://pypi.org/project/sourceinspect/) поддерживают больше интерактивных окружений. Их API и результаты различаются, поэтому каждому потребителю AST или CST приходится выбирать fallback и нормализовать отступы.

[getsources](https://github.com/mutating/getsources) — небольшая Python-библиотека с единым API для получения и нормализации этого текста. Она объединяет `inspect` с резервным механизмом на основе `dill` и отдельно умеет удалять общий начальный отступ. [Релиз](https://github.com/mutating/getsources/releases/tag/0.0.1) доступен на GitHub.

## Получение и нормализация исходника

`getsource(function)` сначала вызывает `inspect.getsource()`, а после `OSError` переключается на `dill.source.getsource()`, в том числе для определений из истории классического REPL.

`getclearsource(function)` удаляет отступ внешнего класса или функции, сохраняя отступы внутри тела функции.

```python
from getsources import getclearsource

class Parser:
    @staticmethod
    def parse(value):
        return int(value)

print(getclearsource(Parser.parse))
#> def parse(value):
#>     return int(value)
```

Полученный текст можно разобрать, не восстанавливая окружающий класс.

Библиотека работает с определёнными в Python функциями, включая методы и lambda, но не с модулями, классами, builtins и произвольными callable. Lambda всё ещё может вернуться вместе с окружающим statement, например `handler = lambda value: value + 1`.

## Как выбрать инструмент восстановления исходника

Для обычного файлового кода проекту может быть достаточно `textwrap.dedent(inspect.getsource(function))`. `inspect.getsourcelines()` также возвращает номера строк и поддерживает больше видов объектов, а прямое использование [dill](https://dill.readthedocs.io/en/latest/dill.html#module-dill.source) даёт больше настроек восстановления.

[sourceinspect](https://pypi.org/project/sourceinspect/) поддерживает больше интерактивных оболочек. [executing](https://github.com/alexmojaki/executing) и [stack-data](https://github.com/alexmojaki/stack_data) лучше подходят для анализа frame или traceback, а [cloudpickle](https://github.com/cloudpipe/cloudpickle) переносит поведение между процессами вместо восстановления читаемого исходника.

Интерактивное восстановление остаётся best-effort. У динамического кода из `exec` или `eval` может не быть надёжного происхождения, сопоставление lambda с историей может быть неоднозначным, а файл на диске — уже не соответствовать загруженному code object. Это восстановление исходника, а не декомпиляция bytecode.

## Для чего предназначен getsources

Пакет даёт AST- и CST-библиотекам единый API для функций из модулей, вложенных областей и классического REPL.
