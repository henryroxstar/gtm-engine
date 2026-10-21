<template>
  <div class="glow card title">
    <slot />
  </div>
</template>

<style scoped>
@keyframes fade-in {
  from { opacity: 0; filter: blur(8px); }
  to { opacity: 1; filter: blur(0); }
}
.card { backdrop-filter: blur(12px); }
.glow { filter: drop-shadow(0 0 4px rgba(255, 255, 255, 0.5)); }
.title { background-clip: text; -webkit-background-clip: text; color: transparent; }
</style>
