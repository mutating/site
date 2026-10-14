# GPU timing симуляции Muta

Диагностика измеряет реальное время выполнения одного полного кадра на GPU:
surface pass, два scatter pass и финальный composite pass. Она использует
`EXT_disjoint_timer_query_webgl2`, читает результаты асинхронно и не вызывает
`gl.finish()` или другие блокирующие операции.

## Включение

По умолчанию GPU timing выключен. Для отдельной страницы симуляции добавьте
`gpu-timing=1`:

```text
http://localhost:1313/muta-tentacles.html?gpu-timing=1
```

На уже открытой главной странице можно перезагрузить только iframe симуляции из
DevTools Console:

```js
const frame = document.querySelector(".contact__tentacles");
const url = new URL(frame.src);
url.searchParams.set("gpu-timing", "1");
frame.src = url;
```

Обычный режим берёт один замер на каждые 30 отрисованных кадров. Четыре query
создаются заранее; результат проверяется в следующих кадрах без ожидания GPU.

## Чтение результата

Откройте DevTools Console внутри страницы симуляции и выполните:

```js
const data = document.querySelector("#muta").dataset;
({
  status: data.gpuTiming,
  latestMs: data.gpuMs,
  averageMs: data.gpuMsAverage,
  samples: data.gpuTimingSamples,
  pending: data.gpuTimingPending,
  dropped: data.gpuTimingDroppedSamples,
  disjoint: data.gpuTimingDisjointCount,
  interval: data.gpuTimingSampleInterval,
});
```

Возможные значения `status`:

- `disabled` — диагностика не запрошена;
- `enabled` — расширение поддерживается, замеры выполняются;
- `unsupported` — браузер или GPU не поддерживает расширение; симуляция при
  этом продолжает работать как обычно.

`latestMs` — последний валидный GPU-замер, `averageMs` — среднее всех валидных
замеров с момента загрузки. Результаты могут появиться только через несколько
кадров. Значение `disjoint` больше нуля означает, что GPU-таймер сбрасывался;
такие замеры автоматически отбрасываются.

## Выключение

Удалите `gpu-timing=1` из URL, задайте `gpu-timing=0` или просто перезагрузите
обычную главную страницу. В выключенном режиме расширение таймера не
запрашивается, query не создаются и код опроса не исполняется.

Параметр `gpu-timing-interval=N` меняет интервал только вместе с
`manual-animation-frame=1`. Это тестовый режим для автоматических проверок;
production-страница всегда использует интервал 30 кадров.

