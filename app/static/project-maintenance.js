/* Absolute local times for UTC schedules; no background requests. */
document.querySelectorAll('time[data-local-time]').forEach(function (element) {
  element.textContent = new Date(element.dateTime).toLocaleString();
});
