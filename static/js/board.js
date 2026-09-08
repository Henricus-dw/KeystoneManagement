// Kanban drag-and-drop + quick add. Talks to /api/tasks/*.
(function () {
  const board = document.getElementById("board");
  if (!board) return;
  const projectId = board.dataset.project;
  let dragged = null;

  function recount() {
    board.querySelectorAll(".col").forEach((col) => {
      const n = col.querySelectorAll(".kcard").length;
      const c = col.querySelector("[data-count]");
      if (c) c.textContent = n;
    });
  }

  function updateProgress(pct) {
    const bar = document.querySelector("#progressBar > i");
    const label = document.getElementById("progressLabel");
    if (bar) bar.style.width = pct + "%";
    if (label) label.textContent = pct + "% complete";
  }

  // ---- drag handlers ----
  board.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".kcard");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
  });
  board.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    document.querySelectorAll(".col-body.drag-over").forEach((b) => b.classList.remove("drag-over"));
    dragged = null;
  });

  board.querySelectorAll(".col-body").forEach((body) => {
    body.addEventListener("dragover", (e) => {
      e.preventDefault();
      body.classList.add("drag-over");
      const after = afterElement(body, e.clientY);
      const addBtn = body.querySelector(".add-card");
      if (!dragged) return;
      if (after == null) body.insertBefore(dragged, addBtn);
      else body.insertBefore(dragged, after);
    });
    body.addEventListener("dragleave", (e) => {
      if (!body.contains(e.relatedTarget)) body.classList.remove("drag-over");
    });
    body.addEventListener("drop", async (e) => {
      e.preventDefault();
      body.classList.remove("drag-over");
      if (!dragged) return;
      const id = dragged.dataset.id;
      const status = body.dataset.status;
      const order = [...body.querySelectorAll(".kcard")].indexOf(dragged);
      recount();
      try {
        const res = await fetch(`/api/tasks/${id}/move`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, order }),
        });
        const data = await res.json();
        if (data.ok) updateProgress(data.progress);
      } catch (_) { /* keep optimistic UI */ }
    });
  });

  function afterElement(container, y) {
    const cards = [...container.querySelectorAll(".kcard:not(.dragging)")];
    return cards.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) return { offset, element: child };
      return closest;
    }, { offset: -Infinity }).element || null;
  }

  // ---- new-task modal ----
  const modal = document.getElementById("taskModal");
  const me = modal ? modal.dataset.me : null;
  const elTitle = document.getElementById("ntTitle");
  const elColumn = document.getElementById("ntColumn");
  const elPriority = document.getElementById("ntPriority");
  const elDue = document.getElementById("ntDue");
  const elAssignees = document.getElementById("ntAssignees");
  let pendingStatus = "Todo";
  let pendingBtn = null;

  function openModal(status, addBtn) {
    pendingStatus = status;
    pendingBtn = addBtn;
    elColumn.textContent = status;
    elTitle.value = "";
    elDue.value = "";
    elPriority.value = "Medium";
    // reset assignees -> only the creator checked
    elAssignees.querySelectorAll("input[type=checkbox]").forEach((c) => {
      c.checked = c.value === me;
    });
    modal.hidden = false;
    setTimeout(() => elTitle.focus(), 30);
  }
  function closeModal() { modal.hidden = true; pendingBtn = null; }

  board.addEventListener("click", (e) => {
    const btn = e.target.closest(".add-card");
    if (btn) openModal(btn.dataset.add, btn);
  });

  if (modal) {
    document.getElementById("ntCancel").addEventListener("click", closeModal);
    document.getElementById("ntClose").addEventListener("click", closeModal);
    modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.hidden) closeModal(); });
    elTitle.addEventListener("keydown", (e) => { if (e.key === "Enter") createTask(); });
    document.getElementById("ntCreate").addEventListener("click", createTask);
  }

  async function createTask() {
    const title = elTitle.value.trim();
    if (!title) { elTitle.focus(); return; }
    const assignees = [...elAssignees.querySelectorAll("input:checked")].map((c) => Number(c.value));
    const res = await fetch("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: Number(projectId),
        title,
        status: pendingStatus,
        priority: elPriority.value,
        due_date: elDue.value || null,
        assignees,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      buildCard(pendingBtn, data.task);
      updateProgress(data.progress);
      closeModal();
    }
  }

  function buildCard(addBtn, task) {
    const card = document.createElement("a");
    card.className = "kcard";
    card.href = `/tasks/${task.id}`;
    card.draggable = true;
    card.dataset.id = task.id;

    const prio = ["High", "Critical"].includes(task.priority)
      ? `<span class="badge s-${task.priority.toLowerCase()}">${task.priority}</span>` : "";
    const avatars = (task.assignees || []).slice(0, 3).map((a) =>
      `<div class="avatar sm" style="background:${a.accent}" title="${escapeHtml(a.name)}">${escapeHtml(a.initials)}</div>`
    ).join("");
    const due = task.due ? `<span class="dim mono" style="font-size:10.5px">${escapeHtml(task.due)}</span>` : "";

    card.innerHTML =
      `<div class="between"><span class="code">${task.code}</span>${prio}</div>` +
      `<div class="ktitle">${escapeHtml(task.title)}</div>` +
      `<div class="kfoot"><div class="avatar-stack">${avatars}</div>${due}</div>`;
    addBtn.parentNode.insertBefore(card, addBtn);
    recount();
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : s;
    return d.innerHTML;
  }
})();
