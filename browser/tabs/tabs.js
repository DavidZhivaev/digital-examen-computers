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

let urlRevision = ''
let urlBuffer = ''
let urlGroups = []
let requestFilters = ''

function compilePatterns(lines) {
  const included = []
  const excluded = []
  for (const line of lines) {
    const exception = line.startsWith('!')
    const pattern = exception ? line.slice(1).trim() : line
    const parts = pattern.split('*').filter(Boolean)
    const target = exception ? excluded : included
    target.push(parts)
  }
  return { included, excluded }
}

function matchesParts(url, parts) {
  let offset = 0
  for (const part of parts) {
    const match = url.indexOf(part, offset)
    if (match < 0) return false
    offset = match + part.length
  }
  return true
}

function matchesList(url, { included, excluded }) {
  return included.some(parts => matchesParts(url, parts))
    && !excluded.some(parts => matchesParts(url, parts))
}

function allowedUrl(url) {
  const host = new URL(url).hostname.toLowerCase().replace(/\.$/, '')
  const matchesHost = (pattern) => {
    if (pattern === '*') return true
    const tail = pattern.replace(/^\*?\./, '')
    return host === tail || (tail !== pattern && host.endsWith('.' + tail))
  }
  return urlGroups.some(({ allow, domains }) => (
    matchesList(url, allow)
    || (domains.some((pattern) => !pattern.startsWith('!') && matchesHost(pattern))
        && !domains.some((pattern) => pattern.startsWith('!') && matchesHost(pattern.slice(1))))
  ))
}

function blockedUrl(url) {
  return !allowedUrl(url) && urlGroups.some(({ deny }) => matchesList(url, deny))
}

function checkRequest({ url }) {
  if (blockedUrl(url)) return { cancel: true }
}

function requestFilter(pattern) {
  const hint = pattern.match(/(?:[A-Za-z0-9]\/|\/(?=[A-Za-z0-9._~-]+\?))([A-Za-z0-9._~/?&=+-]+)/)
  return hint ? `*://*/*${hint[1]}*` : '*://*/*'
}

function setUrlGroups(groups) {
  const positive = groups.flatMap(group => group.deny.filter(line => !line.startsWith('!')))
  urlGroups = groups.map(({ allow, deny, domains }) => ({
    allow: compilePatterns(allow), deny: compilePatterns(deny), domains: Array.isArray(domains) ? domains : [],
  }))
  const filters = [...new Set(positive.map(requestFilter))]
  const urls = filters.includes('*://*/*') ? ['*://*/*'] : filters
  const signature = urls.join(' ')
  if (signature === requestFilters) return
  const request = api.webRequest.onBeforeRequest
  if (requestFilters) request.removeListener(checkRequest)
  if (urls.length) request.addListener(checkRequest, { urls }, ['blocking'])
  requestFilters = signature
}

function prohibited(title) {
  return rules.some(rule => title.toLowerCase().includes(rule.toLowerCase()))
}

function shouldClose(tab) {
  if (!schoolSession || !/^https?:/.test(tab.url ?? '') || allowedUrl(tab.url)) return false
  return urlGroups.some(({ deny }) => matchesList(tab.url, deny)) || Boolean(tab.title && prohibited(tab.title))
}

api.tabs.onUpdated.addListener((id, change, tab) => {
  if ((change.title !== undefined || change.url !== undefined) && shouldClose(tab)) {
    api.tabs.remove(id).catch(() => {})
  }
})

async function sync() {
  if (!port || busy) return
  busy = true
  try {
    const snapshots = await api.tabs.query({})
    const next = new Map()
    for (const tab of snapshots) {
      if (!/^https?:/.test(tab.url ?? '')) continue
      if (shouldClose(tab)) {
        await api.tabs.remove(tab.id).catch(() => {})
        continue
      }
      if (tab.incognito || !tab.title) continue
      const previous = sessions.get(tab.id)
      const token = previous?.url === tab.url && previous.title === tab.title
        ? previous.token : crypto.randomUUID().replaceAll('-', '')
      next.set(tab.id, { token, title: tab.title, url: tab.url })
    }
    sessions.clear()
    for (const [id, item] of next) sessions.set(id, item)
    port?.postMessage({
      profile, browser: browserName, done, url_revision: urlRevision, url_offset: urlBuffer.length,
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
    urlRevision = ''
    urlBuffer = ''
    setUrlGroups([])
    clearInterval(timer)
  })
  port.onMessage.addListener(async (message) => {
    schoolSession = Array.isArray(message.rules)
    rules = schoolSession ? message.rules : []
    const update = message.url_policy
    if (!schoolSession) {
      urlRevision = ''
      urlBuffer = ''
      if (urlGroups.length) setUrlGroups([])
    } else if (update && (update.offset === 0 ||
      (update.revision === urlRevision && update.offset === urlBuffer.length))) {
      if (update.offset === 0) urlBuffer = ''
      urlRevision = update.revision
      urlBuffer += update.chunk
      if (update.complete) setUrlGroups(JSON.parse(urlBuffer))
    }
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
