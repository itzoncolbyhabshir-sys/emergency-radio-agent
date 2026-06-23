import { createRouter, createWebHashHistory } from 'vue-router'
import CommandScreen from '../views/CommandScreen.vue'

const routes = [
  { path: '/', name: 'CommandScreen', component: CommandScreen },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
