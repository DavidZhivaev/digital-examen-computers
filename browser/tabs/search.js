const browserApi = globalThis.browser ?? globalThis.chrome

function searchRedirect(query) {
  if (typeof query !== 'string') return null
  const counted = query.replace(/[\s\u00ad\u200b-\u200d\u2060]/gu, '')
  if (Array.from(counted).length <= 150) return null
  const target = new URL('https://yandex.ru/search/')
  target.searchParams.set('text', query)
  return target.href
}

function managedSession() {
  return browserApi.runtime.sendMessage({ type: 'school-session' }).catch(() => false)
}

const session = managedSession()
let active = false
session.then(value => { active = value })

if (location.pathname === '/search') {
  const target = searchRedirect(new URLSearchParams(location.search).get('q'))
  if (target) session.then(allowed => { if (allowed) location.replace(target) })
}

document.addEventListener('submit', (event) => {
  const form = event.target
  if (!active || !(form instanceof HTMLFormElement)) return
  const action = new URL(form.action)
  if (action.origin !== location.origin || action.pathname !== '/search') return
  const target = searchRedirect(new FormData(form).get('q'))
  if (!target) return
  event.preventDefault()
  event.stopImmediatePropagation()
  location.assign(target)
}, true)
