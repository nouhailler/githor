// Filtre la liste des dépôts par nom, en direct — sans aller-retour serveur.
// Pas de framework : ~80 lignes ne justifient aucune dépendance.
(function () {
  "use strict";

  const filter = document.getElementById("filter");
  const table = document.querySelector("table.repositories");
  const count = document.getElementById("count");
  if (!filter || !table) {
    return;
  }

  const rows = Array.from(table.querySelectorAll("tbody tr[data-name]"));

  filter.addEventListener("input", function () {
    const needle = filter.value.trim().toLowerCase();
    let visible = 0;
    for (const row of rows) {
      const matches = row.dataset.name.includes(needle);
      row.hidden = !matches;
      if (matches) {
        visible += 1;
      }
    }
    if (count) {
      count.textContent = visible + " dépôt(s)";
    }
  });
})();
