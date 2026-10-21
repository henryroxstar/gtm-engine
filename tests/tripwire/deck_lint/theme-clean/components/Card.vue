<template>
  <div class="card">
    <span class="pulse-halo" aria-hidden="true" />
    <span class="gradient-text"><slot /></span>
  </div>
</template>

<style scoped>
@keyframes fade-in {
  from { opacity: 0; filter: blur(8px); }
  to { opacity: 1; filter: none; }
}
.card { backdrop-filter: var(--card-blur); }
.pulse-halo { filter: blur(4px); }
.gradient-text { background-clip: text; }
</style>
