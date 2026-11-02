// Usage: node tools/benchmark_frontend.cjs [optional app.js path]
const { performance } = require('node:perf_hooks');
const { harness } = require('./frontend_performance_harness.cjs');

(async () => {
  const values = [];
  let counts;
  for (let i = 0; i < 12; i++) {
    const h = harness(200, process.argv[2]);
    const start = performance.now();
    await h.context.loadSavedMapPins('farmer-a');
    await Promise.all(h.pending);
    await new Promise(resolve => setTimeout(resolve, 0));
    values.push(performance.now() - start);
    counts = h.counts;
    if (h.context.log.length !== 200) throw new Error('Pins were lost');
  }
  values.sort((a, b) => a - b);
  console.log(JSON.stringify({ fixture: '200 saved pins, VM DOM/MapLibre/Firebase doubles', n: values.length,
    median_ms: (values[5] + values[6]) / 2, p95_ms: values[10], counts }, null, 2));
})();
