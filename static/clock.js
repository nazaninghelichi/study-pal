window.MathoclockClock = (function () {
  var TIME_ZONE = 'America/Toronto';

  function timeOfDayLabel(hour) {
    if (hour >= 5 && hour < 11) return 'Early Bird Derivative';
    if (hour >= 11 && hour < 17) return 'Midday Grind';
    if (hour >= 17 && hour < 22) return 'Evening Integral';
    return 'Certified Insomniac';
  }

  function start(readoutId, labelId) {
    var readout = document.getElementById(readoutId);
    var label = labelId ? document.getElementById(labelId) : null;
    if (!readout) return;

    function tick() {
      var now = new Date();
      var parts = new Intl.DateTimeFormat('en-US', {
        timeZone: TIME_ZONE, hour12: false,
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      }).formatToParts(now).reduce(function (acc, p) {
        acc[p.type] = p.value;
        return acc;
      }, {});

      readout.textContent = parts.hour + ':' + parts.minute + ':' + parts.second;
      if (label) {
        label.textContent = timeOfDayLabel(parseInt(parts.hour, 10));
      }
    }
    tick();
    setInterval(tick, 1000);
  }

  return { start: start };
})();
