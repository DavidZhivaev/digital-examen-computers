const api = globalThis.browser ?? globalThis.chrome
const profile = crypto.randomUUID().replaceAll('-', '')
const browserName = navigator.userAgent.includes('Firefox') ? 'Firefox'
  : navigator.userAgent.includes('YaBrowser') ? 'Яндекс'
  : navigator.userAgent.includes('Vivaldi') ? 'Vivaldi'
  : navigator.userAgent.includes('Edg/') ? 'Edge'
  : navigator.userAgent.includes('OPR/') ? 'Opera'
  : navigator.userAgentData?.brands.some(({ brand }) => brand === 'Google Chrome') ? 'Chrome'
  : 'Chromium'
const sessions = new Map()
let rules = []
let done = {}
let port
let timer
let busy = false
let schoolSession = false
let firstReply = []

function prohibited(title) {
  return rules.some(rule => title.toLowerCase().includes(rule.toLowerCase()))
}

async function sync() {
  if (!port || busy) return
  busy = true
  try {
    const snapshots = await api.tabs.query({})
    const next = new Map()
    for (const tab of snapshots) {
      if (!tab.title || !/^https?:/.test(tab.url ?? '')) continue
      if (prohibited(tab.title)) {
        await api.tabs.remove(tab.id).catch(() => {})
        continue
      }
      if (tab.incognito) continue
      const previous = sessions.get(tab.id)
      const token = previous?.url === tab.url && previous.title === tab.title
        ? previous.token : crypto.randomUUID().replaceAll('-', '')
      next.set(tab.id, { token, title: tab.title, url: tab.url })
    }
    sessions.clear()
    for (const [id, item] of next) sessions.set(id, item)
    port?.postMessage({
      profile, browser: browserName, done,
      tabs: [...sessions.values()].slice(0, 120).map(item => ({ id: item.token, title: item.title.slice(0, 160) })),
    })
    done = {}
  } finally {
    busy = false
  }
}

function connect() {
  if (port) return
  port = api.runtime.connectNative('ru.digital_examen.tabs')
  port.onDisconnect.addListener(() => {
    schoolSession = false
    for (const respond of firstReply.splice(0)) respond(false)
    port = undefined
    rules = []
    clearInterval(timer)
  })
  port.onMessage.addListener(async (message) => {
    schoolSession = Array.isArray(message.rules)
    rules = schoolSession ? message.rules : []
    for (const respond of firstReply.splice(0)) respond(schoolSession)
    for (const token of message.close ?? []) {
      const match = [...sessions].find(([, item]) => item.token === token)
      if (!match) { done[token] = false; continue }
      const [id, item] = match
      try {
        const tab = await api.tabs.get(id)
        if (tab.url !== item.url || tab.title !== item.title) { done[token] = false; continue }
        await api.tabs.remove(id)
        done[token] = true
      } catch {
        done[token] = false
      }
    }
  })
  timer = setInterval(sync, 1500)
  sync()
}

api.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type !== 'school-session') return
  if (schoolSession || !port) {
    respond(schoolSession)
    return
  }
  firstReply.push(respond)
  setTimeout(() => {
    const position = firstReply.indexOf(respond)
    if (position >= 0) {
      firstReply.splice(position, 1)
      respond(false)
    }
  }, 2500)
  return true
})

api.alarms.onAlarm.addListener(connect)
api.alarms.create('reconnect', { periodInMinutes: 0.5 })
connect()
