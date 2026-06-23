<template>
  <main class="command-screen">
    <header class="topbar">
      <div class="brand">
        <span class="beacon" :class="{ online: wsConnected }"></span>
        <div>
          <h1>应急无线电台智能体</h1>
          <p>{{ wsConnected ? '实时监听与智能研判在线' : '正在连接指挥链路' }}</p>
        </div>
      </div>
      <div class="top-actions">
        <button class="ghost-btn" type="button" @click="startDemo">演示场景</button>
        <div class="clock">{{ currentTime }}</div>
      </div>
    </header>

    <section class="metrics-strip">
      <div class="metric critical">
        <span class="metric-value">{{ stats.critical }}</span>
        <span class="metric-label">特急</span>
      </div>
      <div class="metric urgent">
        <span class="metric-value">{{ stats.urgent }}</span>
        <span class="metric-label">紧急</span>
      </div>
      <div class="metric normal">
        <span class="metric-value">{{ stats.normal }}</span>
        <span class="metric-label">一般</span>
      </div>
      <div class="metric">
        <span class="metric-value">{{ incidents.length }}</span>
        <span class="metric-label">活跃事件</span>
      </div>
      <div class="metric">
        <span class="metric-value">{{ avgLatency.toFixed(1) }}s</span>
        <span class="metric-label">平均延迟</span>
      </div>
    </section>

    <section class="workspace">
      <section class="feed-panel">
        <div class="section-heading">
          <span>实时通联流</span>
          <small>{{ pipelineStats.total_events || 0 }} 条已研判</small>
        </div>

        <div class="manual-input">
          <input
            v-model="manualText"
            type="text"
            placeholder="输入一条模拟电台通联，例如：徐汇龙华路发生道路塌陷，两人被困"
            @keydown.enter="submitManual"
          />
          <button
            type="button"
            class="voice-btn"
            :class="{ listening: voiceListening }"
            :disabled="!speechSupported"
            @click="toggleVoiceInput"
          >
            {{ voiceListening ? '收音中' : '语音' }}
          </button>
          <button type="button" @click="submitManual">注入</button>
        </div>
        <div class="voice-status" :class="{ active: voiceListening, error: voiceStatusType === 'error' }">
          {{ voiceStatus }}
        </div>

        <section class="transcript-review">
          <div class="review-heading">
            <span>语音识别复核</span>
            <small>{{ transcripts.length }} 条原始片段 · {{ usableTranscriptCount }} 条可入屏</small>
          </div>
          <div class="transcript-list">
            <article
              v-for="item in reviewTranscripts"
              :key="item.id"
              :class="['transcript-item', { muted: !item.usable }]"
            >
              <div class="transcript-meta">
                <span>#{{ item.sequence || '-' }}</span>
                <strong>{{ transcriptSourceLabel(item.source) }}</strong>
                <em>{{ formatTime(item.timestamp) }}</em>
                <small>{{ item.asr_latency?.toFixed?.(2) || '0.00' }}s</small>
              </div>
              <p>{{ item.text }}</p>
              <div class="transcript-actions">
                <span>{{ transcriptStatusLabel(item) }}</span>
                <button type="button" :disabled="!item.usable" @click="useTranscript(item, false)">填入</button>
                <button type="button" :disabled="!item.usable" @click="useTranscript(item, true)">入屏</button>
              </div>
            </article>
            <div v-if="reviewTranscripts.length === 0" class="empty-transcript">
              等待 SDR/麦克风识别片段进入复核队列
            </div>
          </div>
        </section>

        <div class="event-list" ref="eventList">
          <transition-group name="event" tag="div">
            <article
              v-for="event in events"
              :key="event.id"
              :class="['event-row', event.severity]"
            >
              <div class="event-main">
                <div class="event-head">
                  <span :class="['severity', event.severity]">{{ severityLabel(event.severity) }}</span>
                  <span class="event-type">{{ event.event_type }}</span>
                  <span class="event-time">{{ formatTime(event.timestamp) }}</span>
                </div>
                <p class="event-text">{{ event.raw_text }}</p>
                <div class="event-facts">
                  <span>{{ event.location }}</span>
                  <span>{{ event.entities?.casualty || '人员情况待核实' }}</span>
                  <span>置信度 {{ percent(event.confidence) }}</span>
                  <span v-if="event.is_update">同事件第 {{ event.incident_event_count }} 次更新</span>
                </div>
              </div>
              <div class="event-aside">
                <span :class="['status-pill', event.incident_status]">{{ statusLabel(event.incident_status) }}</span>
                <span v-if="event.review_required" class="review-tag">待复核</span>
              </div>
            </article>
          </transition-group>
          <div v-if="events.length === 0" class="empty-state">
            <strong>等待第一条通联</strong>
            <span>可点击“演示场景”或手动注入文本</span>
          </div>
        </div>
      </section>

      <aside class="ops-panel">
        <section class="incident-panel">
          <div class="section-heading">
            <span>处置闭环</span>
            <small>合并同地点同类型事件</small>
          </div>
          <div class="incident-list">
            <article v-for="incident in incidents" :key="incident.incident_id" class="incident-item">
              <div class="incident-title">
                <span :class="['severity-dot', incident.severity]"></span>
                <strong>{{ incident.summary }}</strong>
              </div>
              <div class="incident-meta">
                <span>{{ incident.incident_id }}</span>
                <span>{{ incident.event_count }} 次通联</span>
                <span>{{ statusLabel(incident.status) }}</span>
              </div>
              <div class="resource-line">
                <span v-for="resource in incident.resources.slice(0, 4)" :key="resource">{{ resource }}</span>
              </div>
              <div class="status-actions">
                <button
                  v-for="step in statusSteps"
                  :key="step.value"
                  type="button"
                  :class="{ active: incident.status === step.value }"
                  @click="setIncidentStatus(incident.incident_id, step.value)"
                >
                  {{ step.label }}
                </button>
              </div>
            </article>
            <div v-if="incidents.length === 0" class="empty-compact">暂无活跃事件</div>
          </div>
        </section>

        <section class="map-panel">
          <div class="section-heading">
            <span>{{ mapPanelTitle }}</span>
            <small>{{ mapPanelSubtitle }}</small>
          </div>
          <div
            class="offline-map-frame"
            :class="{
              online: onlineMapConfigured && onlineMapReady && !forceOfflineMap,
              dragging: offlineMapDragging && offlineMapActive,
            }"
          >
            <div
              v-if="onlineMapConfigured"
              ref="onlineMapEl"
              class="online-map"
            ></div>
            <canvas
              v-show="offlineMapActive"
              ref="offlineMapCanvas"
              class="offline-map-canvas"
              width="520"
              height="270"
              @wheel.prevent="handleOfflineMapWheel"
              @pointerdown="handleOfflineMapPointerDown"
              @pointermove="handleOfflineMapPointerMove"
              @pointerup="handleOfflineMapPointerUp"
              @pointerleave="handleOfflineMapPointerUp"
            ></canvas>
            <div v-if="onlineMapConfigured && !onlineMapReady" class="map-loading">
              {{ onlineMapError || '正在加载真实地图底图…' }}
            </div>
            <div v-if="onlineMapConfigured" class="map-switch">
              <button
                type="button"
                :class="{ active: !forceOfflineMap }"
                @click="forceOfflineMap = false"
              >
                在线
              </button>
              <button
                type="button"
                :class="{ active: forceOfflineMap }"
                @click="forceOfflineMap = true"
              >
                离线
              </button>
            </div>
            <div v-if="offlineMapActive" class="map-controls">
              <button type="button" @click="zoomOfflineMap(0.18)">＋</button>
              <button type="button" @click="zoomOfflineMap(-0.18)">－</button>
              <button type="button" @click="resetOfflineMap">复位</button>
            </div>
            <div v-if="offlineMapActive" class="map-hint">{{ offlineMapHint }}</div>
            <div class="map-meta">
              <span>{{ mapModeLabel }}</span>
              <strong>{{ mapCoordinateLabel }}</strong>
            </div>
          </div>
          <div class="map-legend">
            <span><i class="legend-dot critical"></i>特急</span>
            <span><i class="legend-dot urgent"></i>紧急</span>
            <span><i class="legend-dot normal"></i>一般</span>
            <span><i class="legend-dot minor"></i>轻微</span>
          </div>
          <div class="region-map">
            <div
              v-for="region in regions"
              :key="region.name"
              :class="['region', region.level]"
            >
              <span>{{ region.name }}</span>
              <strong>{{ region.count }}</strong>
            </div>
          </div>
        </section>

        <section class="radio-panel">
          <div class="analyzer-shell">
            <div class="analyzer-grip"></div>
            <div class="analyzer-face">
              <div class="analyzer-topline">
                <div>
                  <span class="analyzer-brand">EMR-SDR</span>
                  <strong>应急频谱监测仪</strong>
                </div>
                <small>{{ signalStatus }} · {{ channelFrequencyMHz }} MHz</small>
              </div>

              <div class="channel-strip" v-if="radioChannels.length">
                <button
                  v-for="channel in radioChannels"
                  :key="channel.id"
                  type="button"
                  :class="['channel-tab', channel.status, { active: channel.id === activeChannelId }]"
                  @click="selectRadioChannel(channel.id)"
                >
                  <span>{{ channel.name }}</span>
                  <strong>{{ formatFrequency(channel.frequency_mhz) }}</strong>
                  <small>{{ channelStatusLabel(channel.status) }}</small>
                </button>
              </div>

              <div class="analyzer-body">
                <div class="analyzer-screen">
                  <div class="screen-toolbar">
                    <span>Spectrum Analyzer</span>
                    <span>SPAN 400 kHz</span>
                    <span>RBW 2.4 kHz</span>
                  </div>
                  <div class="screen-grid">
                    <div class="trace-pane">
                      <div class="scope-label">
                        <span>实时频谱</span>
                        <small>{{ activeRadioChannel?.telemetry_source || 'receiver' }}</small>
                      </div>
                      <canvas ref="spectrumCanvas" class="spectrum-canvas" width="480" height="230"></canvas>
                    </div>
                    <div class="stacked-pane">
                      <div class="heat-pane">
                        <div class="scope-label">
                          <span>能量峰值</span>
                          <small>MAX HOLD</small>
                        </div>
                        <canvas ref="heatmapCanvas" class="heatmap-canvas" width="220" height="112"></canvas>
                      </div>
                      <div class="waterfall-pane">
                        <div class="scope-label">
                          <span>瀑布图</span>
                          <small>{{ radioChannels.length }} 路监听</small>
                        </div>
                        <canvas ref="waterfallCanvas" class="waterfall-canvas" width="220" height="118"></canvas>
                      </div>
                    </div>
                  </div>
                  <div class="screen-readouts">
                    <div>
                      <span>RSSI</span>
                      <strong>{{ rssiDbm }} dBm</strong>
                    </div>
                    <div>
                      <span>SNR</span>
                      <strong>{{ snrDb }} dB</strong>
                    </div>
                    <div>
                      <span>Noise</span>
                      <strong>{{ noiseFloorDbm }} dBm</strong>
                    </div>
                    <div class="signal-bars" aria-label="信号强度">
                      <span
                        v-for="bar in signalBars"
                        :key="bar.level"
                        :class="{ active: bar.active }"
                        :style="{ height: `${bar.height}px` }"
                      ></span>
                    </div>
                  </div>
                </div>

                <div class="analyzer-controls">
                  <div class="rx-indicator">
                    <span :class="{ online: radioReceiving }"></span>
                    <strong>{{ radioSnapshot.mode === 'mock' ? 'SIM' : 'RX' }}</strong>
                  </div>
                  <div class="keypad">
                    <button v-for="key in analyzerKeys" :key="key" type="button">{{ key }}</button>
                  </div>
                  <div class="tuning-dial">
                    <span></span>
                  </div>
                  <div class="radar-pane">
                    <div class="scope-label">
                      <span>雷达态势</span>
                    </div>
                    <canvas ref="radarCanvas" class="radar-canvas" width="180" height="180"></canvas>
                  </div>
                </div>
              </div>
            </div>
            <div class="analyzer-grip"></div>
          </div>
        </section>

        <section class="decision-panel">
          <div class="section-heading">
            <span>最新处置建议</span>
            <small>{{ latestEvent?.reason || '等待研判结果' }}</small>
          </div>
          <ol v-if="latestEvent" class="action-list">
            <li v-for="action in latestEvent.actions" :key="action">{{ action }}</li>
          </ol>
          <div v-if="latestEvent" class="keyword-line">
            <span v-for="keyword in latestEvent.keywords" :key="keyword">{{ keyword }}</span>
          </div>
        </section>
      </aside>
    </section>

    <footer class="ticker">
      <div class="ticker-track">
        <span v-for="item in tickerItems" :key="item.id">
          [{{ severityLabel(item.severity) }}] {{ item.text }}
        </span>
      </div>
    </footer>
  </main>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'

const events = ref([])
const incidents = ref([])
const transcripts = ref([])
const currentTime = ref('')
const wsConnected = ref(false)
const pipelineStats = ref({})
const manualText = ref('')
const voiceListening = ref(false)
const voiceStatus = ref('语音注入：点击“语音”，说完会自动进入大屏')
const voiceStatusType = ref('idle')
const eventList = ref(null)
const tickerItems = ref([])
const radioSnapshot = ref({ active_channel_id: '', channels: [], mode: 'receiver' })
const selectedChannelId = ref('')
const waterfallRows = ref([])
const spectrumCanvas = ref(null)
const heatmapCanvas = ref(null)
const waterfallCanvas = ref(null)
const radarCanvas = ref(null)
const offlineMapCanvas = ref(null)
const onlineMapEl = ref(null)
const onlineMapReady = ref(false)
const onlineMapError = ref('')
const forceOfflineMap = ref(false)
const offlineMapZoom = ref(1)
const offlineMapPan = ref({ x: 0, y: 0 })
const offlineMapDragging = ref(false)
const offlineMapLastPointer = ref(null)

const statusSteps = [
  { value: 'new', label: '新告警' },
  { value: 'confirmed', label: '已确认' },
  { value: 'dispatched', label: '已派遣' },
  { value: 'handling', label: '处置中' },
  { value: 'resolved', label: '已完成' },
]

const analyzerKeys = ['Freq', 'Span', 'Peak', 'Trace', 'BW', 'Avg', 'Hold', 'Run', 'Save', 'Mark', 'Mode', 'Esc']

const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
const WS_URL = import.meta.env.VITE_WS_URL || `${wsProtocol}://${window.location.host}/ws`
const ADMIN_TOKEN = import.meta.env.VITE_ADMIN_TOKEN || ''
const MAP_PROVIDER = String(import.meta.env.VITE_MAP_PROVIDER || '').toLowerCase()
const AMAP_KEY = import.meta.env.VITE_AMAP_KEY || ''
const AMAP_SECURITY_CODE = import.meta.env.VITE_AMAP_SECURITY_CODE || ''
const BAIDU_AK = import.meta.env.VITE_BAIDU_AK || ''

let ws = null
let reconnectTimer = null
let statsTimer = null
let clockTimer = null
let radioFrame = null
let onlineMap = null
let onlineMapProvider = ''
let onlineMapMarkers = []
let voiceRecognition = null
let voiceSubmitted = false

const latestEvent = computed(() => events.value[0])
const speechSupported = computed(() => (
  typeof window !== 'undefined'
  && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)
))
const reviewTranscripts = computed(() => transcripts.value.slice(0, 18))
const usableTranscriptCount = computed(() => transcripts.value.filter(item => item.usable).length)

const stats = computed(() => ({
  critical: events.value.filter(event => event.severity === 'critical').length,
  urgent: events.value.filter(event => event.severity === 'urgent').length,
  normal: events.value.filter(event => event.severity === 'normal').length,
  minor: events.value.filter(event => event.severity === 'minor').length,
}))

const avgLatency = computed(() => {
  const latencies = events.value.map(event => event.pipeline_latency).filter(Boolean)
  if (!latencies.length) return 0
  return latencies.reduce((sum, value) => sum + value, 0) / latencies.length
})

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

const radioChannels = computed(() => radioSnapshot.value.channels || [])

const activeRadioChannel = computed(() => {
  const channels = radioChannels.value
  if (!channels.length) return null
  return channels.find(channel => channel.id === selectedChannelId.value)
    || channels.find(channel => channel.id === radioSnapshot.value.active_channel_id)
    || channels[0]
})

const activeChannelId = computed(() => activeRadioChannel.value?.id || '')

const activeSpectrum = computed(() => {
  const spectrum = activeRadioChannel.value?.spectrum
  return Array.isArray(spectrum) ? spectrum : []
})

const channelFrequencyMHz = computed(() => formatFrequency(activeRadioChannel.value?.frequency_mhz))

const rssiValue = computed(() => {
  const value = activeRadioChannel.value?.rssi_dbm
  return Number.isFinite(Number(value)) ? Number(value) : -120
})

const signalStrength = computed(() => {
  if (!activeRadioChannel.value || activeRadioChannel.value.rssi_dbm == null) return 0
  return Math.round(clamp(((rssiValue.value + 120) / 70) * 100, 0, 100))
})

const signalBars = computed(() => (
  [1, 2, 3, 4, 5].map(level => ({
    level,
    height: 12 + level * 7,
    active: signalStrength.value >= 18 + level * 13,
  }))
))

const rssiDbm = computed(() => (
  activeRadioChannel.value?.rssi_dbm == null ? '--' : Math.round(Number(activeRadioChannel.value.rssi_dbm))
))

const snrDb = computed(() => (
  activeRadioChannel.value?.snr_db == null ? '--' : Math.round(Number(activeRadioChannel.value.snr_db))
))

const noiseFloorDbm = computed(() => (
  activeRadioChannel.value?.noise_floor_dbm == null ? '--' : Math.round(Number(activeRadioChannel.value.noise_floor_dbm))
))

const radioReceiving = computed(() => activeRadioChannel.value?.status === 'receiving')

const signalStatus = computed(() => {
  if (!wsConnected.value) return '链路离线'
  const status = activeRadioChannel.value?.status
  if (status === 'receiving') return '正在接收'
  if (status === 'stale') return '遥测中断'
  if (status === 'scanning') return '扫描监听'
  return '等待接收'
})

const radarContacts = computed(() => Math.min(incidents.value.length || events.value.length, 8))
const mapContactCount = computed(() => (incidents.value.length || events.value.length))
const onlineMapConfigured = computed(() => (
  (MAP_PROVIDER === 'amap' && Boolean(AMAP_KEY))
  || (MAP_PROVIDER === 'baidu' && Boolean(BAIDU_AK))
))
const offlineMapActive = computed(() => forceOfflineMap.value || !onlineMapConfigured.value || Boolean(onlineMapError.value))
const mapModeLabel = computed(() => {
  if (forceOfflineMap.value) return 'OFFLINE'
  if (!onlineMapConfigured.value) return 'OFFLINE'
  if (onlineMapError.value) return 'FALLBACK'
  return MAP_PROVIDER === 'baidu' ? 'BAIDU' : 'AMAP'
})
const mapCoordinateLabel = computed(() => {
  if (forceOfflineMap.value || !onlineMapConfigured.value || onlineMapError.value) return '4326 / LOCAL'
  return MAP_PROVIDER === 'baidu' ? 'BD-09 / ONLINE' : 'GCJ-02 / ONLINE'
})
const mapPanelTitle = computed(() => (
  onlineMapConfigured.value && !onlineMapError.value && !forceOfflineMap.value ? '上海实时态势地图' : '上海离线态势地图'
))
const mapPanelSubtitle = computed(() => (
  `${onlineMapConfigured.value && !onlineMapError.value && !forceOfflineMap.value ? '在线底图' : '全市离线矢量网格'} · ${mapContactCount.value} 个点位`
))
const offlineMapHint = computed(() => (
  `上海本地矢量底图 · ${offlineMapZoom.value.toFixed(1)}x · 可拖拽缩放`
))

const shanghaiRegionAliases = {
  浦东: ['浦东', '陆家嘴', '张江', '临港', '川沙', '外高桥', '世纪大道'],
  黄浦: ['黄浦', '人民广场', '外滩', '南京路', '中心广场', '豫园'],
  徐汇: ['徐汇', '徐家汇', '漕河泾', '龙华', '衡山路', '上海南站'],
  杨浦: ['杨浦', '五角场', '控江', '复旦', '同济', '四平路'],
  闵行: ['闵行', '莘庄', '虹桥', '七宝', '浦江镇', '沪闵路'],
}

const shanghaiCenter = [121.4737, 31.2304]

const shanghaiMapPoints = [
  { name: '浦东陆家嘴中心广场', region: '浦东', keywords: ['浦东陆家嘴中心广场', '陆家嘴', '中心广场'], position: [121.501, 31.238] },
  { name: '浦东张江医院', region: '浦东', keywords: ['浦东张江医院', '张江医院', '张江'], position: [121.596, 31.204] },
  { name: '黄浦外滩商场', region: '黄浦', keywords: ['黄浦外滩商场', '外滩商场', '外滩'], position: [121.491, 31.239] },
  { name: '徐汇龙华路', region: '徐汇', keywords: ['徐汇龙华路', '龙华路', '徐汇'], position: [121.452, 31.176] },
  { name: '杨浦大桥', region: '杨浦', keywords: ['杨浦大桥', '杨浦'], position: [121.539, 31.258] },
  { name: '闵行虹桥站', region: '闵行', keywords: ['闵行虹桥站', '虹桥站', '虹桥', '闵行'], position: [121.327, 31.194] },
]

const shanghaiRegionCenters = {
  浦东: [121.62, 31.23],
  黄浦: [121.49, 31.23],
  徐汇: [121.44, 31.18],
  杨浦: [121.53, 31.27],
  闵行: [121.38, 31.11],
}

const regions = computed(() => {
  const names = Object.keys(shanghaiDistrictAliases)
  return names.map(name => {
    const related = incidents.value.filter(incident => {
      return inferRegionName(incident) === name
    })
    const level = related.some(item => item.severity === 'critical')
      ? 'critical'
      : related.some(item => item.severity === 'urgent')
        ? 'urgent'
        : related.length
          ? 'normal'
          : 'idle'
    return { name, count: related.length, level }
  })
})

const offlineMapRegions = [
  {
    name: '闵行',
    center: [0.24, 0.72],
    polygon: [[0.08, 0.58], [0.27, 0.48], [0.42, 0.58], [0.37, 0.88], [0.12, 0.9]],
  },
  {
    name: '徐汇',
    center: [0.4, 0.58],
    polygon: [[0.28, 0.45], [0.45, 0.42], [0.52, 0.56], [0.42, 0.72], [0.28, 0.66]],
  },
  {
    name: '黄浦',
    center: [0.52, 0.46],
    polygon: [[0.44, 0.34], [0.56, 0.3], [0.62, 0.45], [0.54, 0.58], [0.45, 0.52]],
  },
  {
    name: '杨浦',
    center: [0.56, 0.25],
    polygon: [[0.42, 0.12], [0.64, 0.08], [0.74, 0.25], [0.62, 0.38], [0.48, 0.34]],
  },
  {
    name: '浦东',
    center: [0.76, 0.52],
    polygon: [[0.6, 0.24], [0.88, 0.16], [0.96, 0.68], [0.82, 0.9], [0.58, 0.76], [0.62, 0.48]],
  },
]

const shanghaiGeoBounds = {
  lngMin: 120.78,
  lngMax: 122.16,
  latMin: 30.66,
  latMax: 31.9,
}

const shanghaiDistrictAliases = {
  黄浦: ['黄浦', '外滩', '人民广场', '南京路', '豫园', '淮海中路', '新天地'],
  徐汇: ['徐汇', '徐家汇', '龙华', '漕河泾', '衡山路', '上海南站', '田林'],
  长宁: ['长宁', '虹桥路', '中山公园', '古北', '天山', '上海动物园'],
  静安: ['静安', '南京西路', '苏河湾', '大宁', '彭浦', '上海火车站'],
  普陀: ['普陀', '真如', '长风', '桃浦', '曹杨', '长寿路'],
  虹口: ['虹口', '四川北路', '北外滩', '鲁迅公园', '凉城'],
  杨浦: ['杨浦', '五角场', '控江', '复旦', '同济', '四平路', '杨浦大桥'],
  闵行: ['闵行', '莘庄', '虹桥站', '虹桥枢纽', '七宝', '浦江镇', '沪闵路'],
  宝山: ['宝山', '吴淞', '顾村', '月浦', '罗店', '大场'],
  嘉定: ['嘉定', '安亭', '南翔', '江桥', '马陆', '汽车城'],
  浦东: ['浦东', '陆家嘴', '张江', '临港', '川沙', '外高桥', '世纪大道', '浦东机场', '浦东国际机场'],
  金山: ['金山', '金山卫', '朱泾', '枫泾', '亭林', '石化'],
  松江: ['松江', '佘山', '泗泾', '九亭', '松江大学城', '车墩'],
  青浦: ['青浦', '朱家角', '徐泾', '赵巷', '淀山湖', '国家会展中心'],
  奉贤: ['奉贤', '南桥', '海湾', '奉城', '金海', '海啸'],
  崇明: ['崇明', '长兴岛', '横沙岛', '陈家镇', '东平', '堡镇'],
}

const shanghaiDistrictCenters = {
  黄浦: [121.486, 31.231],
  徐汇: [121.437, 31.188],
  长宁: [121.424, 31.22],
  静安: [121.447, 31.229],
  普陀: [121.395, 31.249],
  虹口: [121.505, 31.264],
  杨浦: [121.526, 31.275],
  闵行: [121.382, 31.112],
  宝山: [121.49, 31.405],
  嘉定: [121.265, 31.375],
  浦东: [121.73, 31.16],
  金山: [121.342, 30.741],
  松江: [121.228, 31.032],
  青浦: [121.124, 31.15],
  奉贤: [121.474, 30.918],
  崇明: [121.64, 31.62],
}

const shanghaiKnownPoints = [
  { name: '浦东陆家嘴中心广场', region: '浦东', keywords: ['浦东陆家嘴中心广场', '陆家嘴', '中心广场'], position: [121.501, 31.238] },
  { name: '浦东张江医院', region: '浦东', keywords: ['浦东张江医院', '张江医院', '张江'], position: [121.596, 31.204] },
  { name: '浦东国际机场', region: '浦东', keywords: ['浦东国际机场', '浦东机场'], position: [121.805, 31.144] },
  { name: '临港新片区', region: '浦东', keywords: ['临港', '滴水湖'], position: [121.91, 30.9] },
  { name: '黄浦外滩商场', region: '黄浦', keywords: ['黄浦外滩商场', '外滩商场', '外滩'], position: [121.491, 31.239] },
  { name: '人民广场', region: '黄浦', keywords: ['人民广场'], position: [121.475, 31.232] },
  { name: '徐汇龙华路', region: '徐汇', keywords: ['徐汇龙华路', '龙华路', '龙华'], position: [121.452, 31.176] },
  { name: '上海南站', region: '徐汇', keywords: ['上海南站', '南站'], position: [121.43, 31.154] },
  { name: '杨浦大桥', region: '杨浦', keywords: ['杨浦大桥', '杨浦'], position: [121.539, 31.258] },
  { name: '闵行虹桥站', region: '闵行', keywords: ['闵行虹桥站', '虹桥站', '虹桥枢纽', '虹桥'], position: [121.327, 31.194] },
  { name: '国家会展中心', region: '青浦', keywords: ['国家会展中心', '会展中心'], position: [121.302, 31.195] },
  { name: '奉贤南桥', region: '奉贤', keywords: ['奉贤', '南桥'], position: [121.474, 30.918] },
  { name: '宝山吴淞', region: '宝山', keywords: ['宝山', '吴淞'], position: [121.496, 31.38] },
  { name: '嘉定安亭', region: '嘉定', keywords: ['嘉定', '安亭', '汽车城'], position: [121.166, 31.29] },
  { name: '松江大学城', region: '松江', keywords: ['松江大学城', '松江'], position: [121.214, 31.057] },
  { name: '金山卫', region: '金山', keywords: ['金山卫', '金山'], position: [121.343, 30.728] },
  { name: '崇明陈家镇', region: '崇明', keywords: ['崇明', '陈家镇'], position: [121.8, 31.5] },
]

const shanghaiDistrictShapes = [
  { name: '崇明', center: [121.64, 31.62], polygon: [[121.12, 31.58], [121.36, 31.71], [121.77, 31.74], [122.02, 31.64], [121.93, 31.5], [121.55, 31.48], [121.22, 31.51]] },
  { name: '嘉定', center: [121.265, 31.375], polygon: [[121.05, 31.22], [121.34, 31.2], [121.43, 31.36], [121.35, 31.51], [121.1, 31.5], [120.98, 31.35]] },
  { name: '宝山', center: [121.49, 31.405], polygon: [[121.33, 31.28], [121.57, 31.27], [121.72, 31.39], [121.62, 31.55], [121.38, 31.52], [121.3, 31.39]] },
  { name: '青浦', center: [121.124, 31.15], polygon: [[120.86, 31.02], [121.15, 30.98], [121.32, 31.1], [121.31, 31.26], [121.02, 31.27], [120.82, 31.18]] },
  { name: '松江', center: [121.228, 31.032], polygon: [[121.02, 30.86], [121.31, 30.85], [121.43, 30.99], [121.34, 31.16], [121.12, 31.13], [120.98, 31.0]] },
  { name: '闵行', center: [121.382, 31.112], polygon: [[121.25, 31.0], [121.46, 30.98], [121.54, 31.12], [121.48, 31.24], [121.31, 31.23], [121.22, 31.12]] },
  { name: '长宁', center: [121.424, 31.22], polygon: [[121.36, 31.18], [121.46, 31.17], [121.49, 31.23], [121.43, 31.28], [121.34, 31.25]] },
  { name: '徐汇', center: [121.437, 31.188], polygon: [[121.36, 31.12], [121.48, 31.13], [121.51, 31.2], [121.45, 31.25], [121.35, 31.21]] },
  { name: '普陀', center: [121.395, 31.249], polygon: [[121.31, 31.23], [121.42, 31.21], [121.48, 31.28], [121.41, 31.35], [121.31, 31.32]] },
  { name: '静安', center: [121.447, 31.229], polygon: [[121.41, 31.2], [121.49, 31.2], [121.51, 31.29], [121.43, 31.3], [121.39, 31.25]] },
  { name: '黄浦', center: [121.486, 31.231], polygon: [[121.46, 31.18], [121.52, 31.2], [121.53, 31.25], [121.48, 31.28], [121.44, 31.24]] },
  { name: '虹口', center: [121.505, 31.264], polygon: [[121.47, 31.25], [121.55, 31.24], [121.57, 31.31], [121.5, 31.34], [121.45, 31.3]] },
  { name: '杨浦', center: [121.526, 31.275], polygon: [[121.52, 31.22], [121.64, 31.23], [121.68, 31.32], [121.58, 31.38], [121.5, 31.32]] },
  { name: '浦东', center: [121.73, 31.16], polygon: [[121.52, 30.86], [121.86, 30.79], [122.08, 31.0], [121.98, 31.31], [121.72, 31.42], [121.54, 31.26]] },
  { name: '奉贤', center: [121.474, 30.918], polygon: [[121.28, 30.75], [121.62, 30.73], [121.72, 30.92], [121.55, 31.04], [121.31, 30.99]] },
  { name: '金山', center: [121.342, 30.741], polygon: [[120.98, 30.67], [121.34, 30.65], [121.5, 30.8], [121.31, 30.93], [121.02, 30.87]] },
]

function severityLabel(severity) {
  return { critical: '特急', urgent: '紧急', normal: '一般', minor: '轻微' }[severity] || severity
}

function statusLabel(status) {
  return {
    new: '新告警',
    confirmed: '已确认',
    dispatched: '已派遣',
    handling: '处置中',
    resolved: '已完成',
  }[status] || '新告警'
}

function percent(value) {
  return `${Math.round((value || 0) * 100)}%`
}

function formatTime(iso) {
  const date = new Date(iso)
  return date.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatFrequency(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(3) : '---'
}

function channelStatusLabel(status) {
  return {
    waiting: '待接收',
    scanning: '扫描',
    receiving: '接收中',
    stale: '中断',
  }[status] || '未知'
}

function selectRadioChannel(channelId) {
  selectedChannelId.value = channelId
  waterfallRows.value = []
  const bins = activeSpectrum.value
  if (bins.length > 1) waterfallRows.value.unshift([...bins])
}

function severityColor(severity, alpha = 1) {
  const colors = {
    critical: [255, 77, 77],
    urgent: [255, 173, 59],
    normal: [77, 163, 255],
    minor: [67, 209, 132],
    idle: [47, 72, 88],
  }
  const [r, g, b] = colors[severity] || colors.normal
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function regionLevel(name) {
  return regions.value.find(region => region.name === name)?.level || 'idle'
}

function inferRegionName(item) {
  const text = `${item.location || ''} ${item.summary || ''} ${item.latest_text || ''} ${item.raw_text || ''}`
  for (const [regionName, keywords] of Object.entries(shanghaiDistrictAliases)) {
    if (keywords.some(keyword => text.includes(keyword))) return regionName
  }
  if (text.includes('东')) return '浦东'
  if (text.includes('西') || text.includes('南')) return '闵行'
  if (text.includes('中') || text.includes('中心')) return '黄浦'
  return '黄浦'
}

function shanghaiLocationPointFor(item, index = 0) {
  const text = `${item.location || ''} ${item.summary || ''} ${item.latest_text || ''} ${item.raw_text || ''}`
  const matched = shanghaiKnownPoints.find(point => point.keywords.some(keyword => text.includes(keyword)))
  const base = matched?.position || shanghaiDistrictCenters[inferRegionName(item)] || shanghaiCenter
  const id = item.incident_id || item.id || `${inferRegionName(item)}-${index}`
  const jitter = matched ? 0.0015 : 0.012
  return [
    base[0] + stableOffset(id, jitter),
    base[1] + stableOffset(`${id}-lat`, jitter),
  ]
}

function stableOffset(seed, scale = 1) {
  const text = String(seed || '')
  let hash = 0
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) % 9973
  return ((hash % 100) / 100 - 0.5) * scale
}

function normalizedShanghaiPoint([lng, lat]) {
  const { lngMin, lngMax, latMin, latMax } = shanghaiGeoBounds
  return {
    x: clamp((lng - lngMin) / (lngMax - lngMin), 0.04, 0.96),
    y: clamp(1 - ((lat - latMin) / (latMax - latMin)), 0.06, 0.94),
  }
}

function mapPointFor(item, index) {
  const regionName = inferRegionName(item)
  const normalized = normalizedShanghaiPoint(shanghaiLocationPointFor(item, index))
  return {
    x: normalized.x,
    y: normalized.y,
    regionName,
  }
}

function clampOfflinePan(pan, zoom = offlineMapZoom.value) {
  const extra = Math.max(0, zoom - 1)
  const limitX = 42 + extra * 130
  const limitY = 30 + extra * 86
  return {
    x: clamp(pan.x, -limitX, limitX),
    y: clamp(pan.y, -limitY, limitY),
  }
}

function zoomOfflineMap(delta) {
  if (!offlineMapActive.value) return
  const zoom = clamp(offlineMapZoom.value + delta, 0.88, 2.35)
  offlineMapZoom.value = Number(zoom.toFixed(2))
  offlineMapPan.value = clampOfflinePan(offlineMapPan.value, zoom)
}

function resetOfflineMap() {
  offlineMapZoom.value = 1
  offlineMapPan.value = { x: 0, y: 0 }
  offlineMapDragging.value = false
  offlineMapLastPointer.value = null
}

function handleOfflineMapWheel(event) {
  if (!offlineMapActive.value) return
  zoomOfflineMap(event.deltaY > 0 ? -0.12 : 0.12)
}

function handleOfflineMapPointerDown(event) {
  if (!offlineMapActive.value) return
  offlineMapDragging.value = true
  offlineMapLastPointer.value = { x: event.clientX, y: event.clientY }
  event.currentTarget?.setPointerCapture?.(event.pointerId)
}

function handleOfflineMapPointerMove(event) {
  if (!offlineMapActive.value || !offlineMapDragging.value || !offlineMapLastPointer.value) return
  const dx = event.clientX - offlineMapLastPointer.value.x
  const dy = event.clientY - offlineMapLastPointer.value.y
  offlineMapPan.value = clampOfflinePan({
    x: offlineMapPan.value.x + dx,
    y: offlineMapPan.value.y + dy,
  })
  offlineMapLastPointer.value = { x: event.clientX, y: event.clientY }
}

function handleOfflineMapPointerUp(event) {
  if (!offlineMapDragging.value) return
  offlineMapDragging.value = false
  offlineMapLastPointer.value = null
  event.currentTarget?.releasePointerCapture?.(event.pointerId)
}

function loadExternalScript(id, src, callbackName = '') {
  return new Promise((resolve, reject) => {
    const existing = document.getElementById(id)
    if (existing?.dataset.loaded === 'true') {
      resolve()
      return
    }
    if (existing) existing.remove()

    if (callbackName) {
      window[callbackName] = () => {
        const script = document.getElementById(id)
        if (script) script.dataset.loaded = 'true'
        resolve()
      }
    }

    const script = document.createElement('script')
    script.id = id
    script.async = true
    script.defer = true
    script.src = src
    script.onload = () => {
      script.dataset.loaded = 'true'
      if (!callbackName) resolve()
    }
    script.onerror = () => reject(new Error(`地图脚本加载失败: ${src}`))
    document.head.appendChild(script)
  })
}

function toBaiduCoordinate([lng, lat]) {
  return [lng + 0.0065, lat + 0.006]
}

function markerColor(severity) {
  return {
    critical: '#ff4d4d',
    urgent: '#ffad3b',
    normal: '#4da3ff',
    minor: '#43d184',
  }[severity] || '#4da3ff'
}

function displayMapLocation(item) {
  const location = String(item?.location || '').trim()
  if (location && location !== '未指明' && location !== '未知地点') return location
  const text = `${item?.location || ''} ${item?.summary || ''} ${item?.latest_text || ''} ${item?.raw_text || ''}`
  const matched = shanghaiKnownPoints.find(point => point.keywords.some(keyword => text.includes(keyword)))
  return matched?.name || inferRegionName(item) || '上海'
}

function markerLabel(item) {
  return `${severityLabel(item.severity)} · ${displayMapLocation(item)}`
}

function markerHtml(item) {
  const color = markerColor(item.severity)
  return `
    <div style="
      width:16px;height:16px;border-radius:50%;
      background:${color};border:2px solid #fff;
      box-shadow:0 0 0 8px ${color}33,0 0 18px ${color};
    "></div>
  `
}

async function initOnlineMap() {
  if (!onlineMapConfigured.value || !onlineMapEl.value || onlineMapReady.value) return
  try {
    onlineMapError.value = ''
    if (MAP_PROVIDER === 'amap') {
      if (AMAP_SECURITY_CODE) {
        window._AMapSecurityConfig = { securityJsCode: AMAP_SECURITY_CODE }
      }
      const src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(AMAP_KEY)}&plugin=AMap.Scale,AMap.ToolBar`
      await loadExternalScript('amap-jsapi-v2', src)
      if (!window.AMap) throw new Error('高德地图对象未就绪')
      onlineMapProvider = 'amap'
      onlineMap = new window.AMap.Map(onlineMapEl.value, {
        center: shanghaiCenter,
        zoom: 11,
        viewMode: '2D',
        mapStyle: 'amap://styles/darkblue',
      })
      onlineMap.addControl(new window.AMap.Scale())
      onlineMap.addControl(new window.AMap.ToolBar({ liteStyle: true }))
    } else if (MAP_PROVIDER === 'baidu') {
      const callbackName = `__bdMapReady_${Date.now()}`
      const src = `https://api.map.baidu.com/api?v=1.0&type=webgl&ak=${encodeURIComponent(BAIDU_AK)}&callback=${callbackName}`
      await loadExternalScript('baidu-mapgl-jsapi', src, callbackName)
      if (!window.BMapGL) throw new Error('百度地图对象未就绪')
      onlineMapProvider = 'baidu'
      onlineMap = new window.BMapGL.Map(onlineMapEl.value)
      const [lng, lat] = toBaiduCoordinate(shanghaiCenter)
      onlineMap.centerAndZoom(new window.BMapGL.Point(lng, lat), 11)
      onlineMap.enableScrollWheelZoom(true)
      onlineMap.addControl(new window.BMapGL.ScaleControl())
      onlineMap.addControl(new window.BMapGL.ZoomControl())
    }
    onlineMapReady.value = true
    updateOnlineMapMarkers()
  } catch (error) {
    onlineMapError.value = error?.message || String(error)
    onlineMapReady.value = false
  }
}

function clearOnlineMarkers() {
  if (!onlineMap || !onlineMapMarkers.length) return
  if (onlineMapProvider === 'amap') onlineMap.remove(onlineMapMarkers)
  if (onlineMapProvider === 'baidu') onlineMapMarkers.forEach(marker => onlineMap.removeOverlay(marker))
  onlineMapMarkers = []
}

function updateOnlineMapMarkers() {
  if (!onlineMapReady.value || !onlineMap) return
  clearOnlineMarkers()
  const contacts = (incidents.value.length ? incidents.value : events.value).slice(0, 30)
  if (!contacts.length) return

  if (onlineMapProvider === 'amap') {
    onlineMapMarkers = contacts.map((item, index) => {
      const marker = new window.AMap.Marker({
        position: shanghaiLocationPointFor(item, index),
        content: markerHtml(item),
        offset: new window.AMap.Pixel(-8, -8),
        title: `${item.event_type || '事件'} / ${displayMapLocation(item)}`,
      })
      marker.setLabel({
        direction: 'top',
        offset: new window.AMap.Pixel(0, -8),
        content: `<span style="padding:3px 6px;border-radius:999px;background:rgba(3,10,16,.86);color:#fff;border:1px solid ${markerColor(item.severity)};font-size:12px;">${markerLabel(item)}</span>`,
      })
      marker.on('click', () => {
        const info = new window.AMap.InfoWindow({
          content: `<div style="min-width:220px;"><b>${markerLabel(item)}</b><br/>${item.summary || item.latest_text || item.raw_text || ''}</div>`,
          offset: new window.AMap.Pixel(0, -12),
        })
        info.open(onlineMap, marker.getPosition())
      })
      return marker
    })
    onlineMap.add(onlineMapMarkers)
    onlineMap.setFitView(onlineMapMarkers, false, [36, 36, 36, 36], 10)
  } else if (onlineMapProvider === 'baidu') {
    onlineMapMarkers = contacts.map((item, index) => {
      const [lng, lat] = toBaiduCoordinate(shanghaiLocationPointFor(item, index))
      const point = new window.BMapGL.Point(lng, lat)
      const marker = new window.BMapGL.Marker(point)
      const label = new window.BMapGL.Label(markerLabel(item), {
        position: point,
        offset: new window.BMapGL.Size(10, -24),
      })
      label.setStyle({
        color: '#fff',
        backgroundColor: 'rgba(3,10,16,.86)',
        border: `1px solid ${markerColor(item.severity)}`,
        borderRadius: '999px',
        padding: '3px 6px',
        fontSize: '12px',
      })
      marker.setLabel(label)
      marker.addEventListener('click', () => {
        const info = new window.BMapGL.InfoWindow(`<b>${markerLabel(item)}</b><br/>${item.summary || item.latest_text || item.raw_text || ''}`, {
          width: 260,
          title: item.event_type || '应急事件',
        })
        onlineMap.openInfoWindow(info, point)
      })
      onlineMap.addOverlay(marker)
      return marker
    })
  }
}

function fitCanvas(canvas) {
  const rect = canvas.getBoundingClientRect()
  const dpr = window.devicePixelRatio || 1
  const width = Math.max(1, Math.floor(rect.width * dpr))
  const height = Math.max(1, Math.floor(rect.height * dpr))
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width
    canvas.height = height
  }
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return { ctx, width: rect.width, height: rect.height }
}

function drawWaitingSignal(ctx, width, height, message) {
  ctx.fillStyle = 'rgba(226, 234, 195, 0.64)'
  ctx.font = '12px Microsoft YaHei, Arial'
  ctx.textAlign = 'center'
  ctx.fillText(message, width / 2, height / 2)
  ctx.textAlign = 'left'
}

function drawSpectrum() {
  const canvas = spectrumCanvas.value
  if (!canvas) return
  const fitted = fitCanvas(canvas)
  if (!fitted) return
  const { ctx, width, height } = fitted

  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = '#020303'
  ctx.fillRect(0, 0, width, height)

  ctx.strokeStyle = 'rgba(180, 190, 160, 0.22)'
  ctx.lineWidth = 1
  for (let i = 0; i <= 8; i += 1) {
    const x = (width / 8) * i
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height)
    ctx.stroke()
  }
  for (let i = 0; i <= 6; i += 1) {
    const y = (height / 6) * i
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(width, y)
    ctx.stroke()
  }

  ctx.fillStyle = 'rgba(226, 234, 195, 0.72)'
  ctx.font = '10px Arial'
  ctx.fillText('Ref -30 dBm', 8, 14)
  ctx.fillText(`C ${channelFrequencyMHz.value}`, 8, height - 8)
  ctx.textAlign = 'right'
  ctx.fillText('SPAN 400k', width - 8, height - 8)
  ctx.textAlign = 'left'

  const bins = activeSpectrum.value
  if (bins.length < 2) {
    drawWaitingSignal(ctx, width, height, '等待接收机频谱遥测')
    return
  }

  const points = bins.map((value, index) => {
    const ratio = bins.length === 1 ? 0 : index / (bins.length - 1)
    const level = clamp(Number(value) || 0, 0, 1)
    return {
      x: ratio * width,
      y: height - 18 - level * (height - 36),
    }
  })

  ctx.beginPath()
  ctx.moveTo(0, height - 16)
  points.forEach(point => ctx.lineTo(point.x, point.y))
  ctx.lineTo(width, height - 16)
  ctx.closePath()
  const fill = ctx.createLinearGradient(0, 0, 0, height)
  fill.addColorStop(0, 'rgba(255, 255, 80, 0.92)')
  fill.addColorStop(0.48, 'rgba(255, 220, 20, 0.58)')
  fill.addColorStop(1, 'rgba(255, 240, 20, 0.12)')
  ctx.fillStyle = fill
  ctx.fill()

  ctx.strokeStyle = '#fff238'
  ctx.lineWidth = 1.4
  ctx.shadowColor = 'rgba(255, 240, 20, 0.8)'
  ctx.shadowBlur = 7
  ctx.beginPath()
  points.forEach((point, index) => {
    if (index === 0) ctx.moveTo(point.x, point.y)
    else ctx.lineTo(point.x, point.y)
  })
  ctx.stroke()
  ctx.shadowBlur = 0
}

function heatColor(level) {
  const value = Math.max(0, Math.min(1, level))
  if (value < 0.25) return `rgb(0, ${Math.round(130 + value * 360)}, 56)`
  if (value < 0.5) return `rgb(${Math.round((value - 0.25) * 760)}, 245, 28)`
  if (value < 0.74) return `rgb(255, ${Math.round(235 - (value - 0.5) * 560)}, 12)`
  return `rgb(255, ${Math.round(95 - (value - 0.74) * 220)}, ${Math.round(10 + (value - 0.74) * 260)})`
}

function drawHeatmap() {
  const canvas = heatmapCanvas.value
  if (!canvas) return
  const fitted = fitCanvas(canvas)
  if (!fitted) return
  const { ctx, width, height } = fitted
  const bins = activeSpectrum.value
  const cols = Math.max(2, bins.length || 84)
  const rows = 28
  const cellW = width / cols
  const cellH = height / rows

  ctx.clearRect(0, 0, width, height)
  if (bins.length < 2) {
    ctx.fillStyle = '#020303'
    ctx.fillRect(0, 0, width, height)
    drawWaitingSignal(ctx, width, height, 'NO RF BINS')
    return
  }

  for (let x = 0; x < cols; x += 1) {
    const base = clamp(Number(bins[x]) || 0, 0, 1)
    for (let y = 0; y < rows; y += 1) {
      const vertical = 1 - y / rows
      const level = base * (0.38 + vertical * 0.72)
      ctx.fillStyle = heatColor(level)
      ctx.fillRect(x * cellW, y * cellH, Math.ceil(cellW), Math.ceil(cellH))
    }
  }

  ctx.strokeStyle = 'rgba(255, 255, 255, 0.18)'
  ctx.lineWidth = 1
  for (let i = 1; i < 4; i += 1) {
    const y = (height / 4) * i
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(width, y)
    ctx.stroke()
  }
}

function drawWaterfall() {
  const canvas = waterfallCanvas.value
  if (!canvas) return
  const fitted = fitCanvas(canvas)
  if (!fitted) return
  const { ctx, width, height } = fitted
  const visibleRows = waterfallRows.value.slice(0, 42)
  const rows = Math.max(1, visibleRows.length)
  const cols = Math.max(2, visibleRows[0]?.length || 96)
  const cellW = width / cols
  const cellH = height / rows

  ctx.clearRect(0, 0, width, height)
  if (!waterfallRows.value.length) {
    ctx.fillStyle = '#020303'
    ctx.fillRect(0, 0, width, height)
    drawWaitingSignal(ctx, width, height, 'WAIT WATERFALL')
    return
  }

  visibleRows.forEach((row, y) => {
    for (let x = 0; x < cols; x += 1) {
      const sourceIndex = Math.floor((x / Math.max(1, cols - 1)) * Math.max(0, row.length - 1))
      const level = clamp(Number(row[sourceIndex]) || 0, 0, 1)
      ctx.fillStyle = heatColor(level)
      ctx.fillRect(x * cellW, y * cellH, Math.ceil(cellW), Math.ceil(cellH))
    }
  })
}

function drawMapPolyline(ctx, points, xOf, yOf) {
  ctx.beginPath()
  points.forEach(([x, y], index) => {
    if (index === 0) ctx.moveTo(xOf(x), yOf(y))
    else ctx.lineTo(xOf(x), yOf(y))
  })
  ctx.stroke()
}

function drawRoundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2)
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + width - r, y)
  ctx.quadraticCurveTo(x + width, y, x + width, y + r)
  ctx.lineTo(x + width, y + height - r)
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height)
  ctx.lineTo(x + r, y + height)
  ctx.quadraticCurveTo(x, y + height, x, y + height - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

function drawMapLabel(ctx, text, x, y, options = {}) {
  const {
    align = 'center',
    color = '#d7ecff',
    halo = 'rgba(2, 8, 14, 0.82)',
    size = 11,
    weight = 700,
  } = options
  ctx.save()
  ctx.font = `${weight} ${size}px "Microsoft YaHei", Arial, sans-serif`
  ctx.textAlign = align
  ctx.textBaseline = 'middle'
  if (halo) {
    ctx.lineWidth = Math.max(2, size / 3)
    ctx.strokeStyle = halo
    ctx.strokeText(text, x, y)
  }
  ctx.fillStyle = color
  ctx.fillText(text, x, y)
  ctx.restore()
}

function drawOfflineMap(timestamp) {
  const canvas = offlineMapCanvas.value
  if (!canvas) return
  const fitted = fitCanvas(canvas)
  if (!fitted) return
  const { ctx, width, height } = fitted
  const zoom = offlineMapZoom.value
  const pan = offlineMapPan.value
  const xOf = value => (value - 0.5) * width * zoom + width / 2 + pan.x
  const yOf = value => (value - 0.5) * height * zoom + height / 2 + pan.y
  const roadScale = clamp(zoom, 0.85, 1.35)

  ctx.clearRect(0, 0, width, height)
  const background = ctx.createLinearGradient(0, 0, width, height)
  background.addColorStop(0, '#07121b')
  background.addColorStop(0.52, '#09202a')
  background.addColorStop(1, '#061019')
  ctx.fillStyle = background
  ctx.fillRect(0, 0, width, height)

  const gridStep = 42 * zoom
  const gridOffsetX = ((pan.x % gridStep) + gridStep) % gridStep
  const gridOffsetY = ((pan.y % gridStep) + gridStep) % gridStep
  ctx.strokeStyle = 'rgba(134, 188, 216, 0.09)'
  ctx.lineWidth = 1
  for (let x = gridOffsetX - gridStep; x <= width + gridStep; x += gridStep) {
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height)
    ctx.stroke()
  }
  for (let y = gridOffsetY - gridStep; y <= height + gridStep; y += gridStep) {
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(width, y)
    ctx.stroke()
  }

  ctx.save()
  ctx.fillStyle = 'rgba(12, 47, 43, 0.34)'
  ctx.beginPath()
  ctx.moveTo(xOf(0.02), yOf(0.18))
  ctx.lineTo(xOf(0.18), yOf(0.05))
  ctx.lineTo(xOf(0.63), yOf(0.01))
  ctx.lineTo(xOf(0.98), yOf(0.14))
  ctx.lineTo(xOf(0.99), yOf(0.87))
  ctx.lineTo(xOf(0.72), yOf(0.98))
  ctx.lineTo(xOf(0.16), yOf(0.95))
  ctx.lineTo(xOf(0.01), yOf(0.72))
  ctx.closePath()
  ctx.fill()
  ctx.restore()

  shanghaiDistrictShapes.forEach(region => {
    const level = regionLevel(region.name)
    const count = regions.value.find(item => item.name === region.name)?.count || 0
    const center = normalizedShanghaiPoint(region.center)
    ctx.save()
    ctx.beginPath()
    region.polygon.forEach((point, index) => {
      const { x, y } = normalizedShanghaiPoint(point)
      if (index === 0) ctx.moveTo(xOf(x), yOf(y))
      else ctx.lineTo(xOf(x), yOf(y))
    })
    ctx.closePath()
    ctx.shadowBlur = level === 'idle' ? 0 : 18
    ctx.shadowColor = severityColor(level, 0.28)
    ctx.fillStyle = level === 'idle'
      ? 'rgba(26, 51, 60, 0.36)'
      : severityColor(level, 0.16)
    ctx.strokeStyle = level === 'idle'
      ? 'rgba(132, 176, 198, 0.22)'
      : severityColor(level, 0.72)
    ctx.lineWidth = level === 'idle' ? 1 : 1.8
    ctx.fill()
    ctx.stroke()
    ctx.restore()

    drawMapLabel(ctx, region.name, xOf(center.x), yOf(center.y) - 7, {
      size: zoom > 1.25 ? 12 : 10,
      weight: 800,
      color: '#e6f5ff',
    })
    drawMapLabel(ctx, `${count} 起`, xOf(center.x), yOf(center.y) + 10, {
      size: zoom > 1.25 ? 11 : 9,
      weight: 800,
      color: count ? severityColor(level, 0.98) : 'rgba(190, 218, 231, 0.64)',
    })
  })

  const river = [
    [0.54, 0.02], [0.49, 0.15], [0.52, 0.27], [0.61, 0.36],
    [0.55, 0.47], [0.49, 0.58], [0.58, 0.68], [0.65, 0.82], [0.62, 0.98],
  ]
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.strokeStyle = 'rgba(19, 77, 120, 0.92)'
  ctx.lineWidth = 22 * roadScale
  drawMapPolyline(ctx, river, xOf, yOf)
  ctx.strokeStyle = 'rgba(77, 181, 255, 0.72)'
  ctx.lineWidth = 13 * roadScale
  drawMapPolyline(ctx, river, xOf, yOf)
  ctx.strokeStyle = 'rgba(207, 237, 255, 0.68)'
  ctx.lineWidth = 2
  drawMapPolyline(ctx, river, xOf, yOf)
  drawMapLabel(ctx, '黄浦江', xOf(0.58), yOf(0.43), {
    color: 'rgba(220, 244, 255, 0.82)',
    size: 10,
    weight: 800,
  })

  const geoPolyline = points => points.map(point => {
    const { x, y } = normalizedShanghaiPoint(point)
    return [x, y]
  })

  const huangpuRiverGeo = geoPolyline([
    [121.49, 31.36], [121.48, 31.29], [121.51, 31.25], [121.49, 31.22],
    [121.53, 31.17], [121.48, 31.1], [121.55, 31.0], [121.6, 30.88],
  ])
  ctx.strokeStyle = 'rgba(77, 181, 255, 0.42)'
  ctx.lineWidth = 8 * roadScale
  drawMapPolyline(ctx, huangpuRiverGeo, xOf, yOf)
  ctx.strokeStyle = 'rgba(207, 237, 255, 0.54)'
  ctx.lineWidth = 1.6
  drawMapPolyline(ctx, huangpuRiverGeo, xOf, yOf)

  const metroArteries = [
    geoPolyline([[120.92, 31.18], [121.18, 31.17], [121.47, 31.23], [121.8, 31.14], [122.05, 31.0]]),
    geoPolyline([[121.16, 31.48], [121.28, 31.33], [121.47, 31.23], [121.62, 31.02], [121.48, 30.75]]),
    geoPolyline([[121.0, 30.86], [121.22, 31.03], [121.47, 31.23], [121.65, 31.35], [121.92, 31.55]]),
  ]
  metroArteries.forEach(line => {
    ctx.strokeStyle = 'rgba(255, 203, 104, 0.28)'
    ctx.lineWidth = 2.2 * roadScale
    drawMapPolyline(ctx, line, xOf, yOf)
  })

  const roadRings = [
    { name: '内环', cx: 0.51, cy: 0.5, rx: 0.2, ry: 0.23, rotation: -0.22, color: 'rgba(255, 213, 112, 0.78)' },
    { name: '中环', cx: 0.54, cy: 0.51, rx: 0.32, ry: 0.35, rotation: -0.16, color: 'rgba(255, 255, 255, 0.35)' },
    { name: '外环', cx: 0.55, cy: 0.5, rx: 0.44, ry: 0.46, rotation: -0.12, color: 'rgba(142, 181, 198, 0.22)' },
  ]
  roadRings.forEach(road => {
    ctx.save()
    ctx.setLineDash(road.name === '外环' ? [6, 7] : [])
    ctx.strokeStyle = 'rgba(3, 10, 16, 0.72)'
    ctx.lineWidth = 5 * roadScale
    ctx.beginPath()
    ctx.ellipse(xOf(road.cx), yOf(road.cy), width * road.rx * zoom, height * road.ry * zoom, road.rotation, 0, Math.PI * 2)
    ctx.stroke()
    ctx.strokeStyle = road.color
    ctx.lineWidth = road.name === '内环' ? 2.4 * roadScale : 1.7 * roadScale
    ctx.beginPath()
    ctx.ellipse(xOf(road.cx), yOf(road.cy), width * road.rx * zoom, height * road.ry * zoom, road.rotation, 0, Math.PI * 2)
    ctx.stroke()
    ctx.restore()
    drawMapLabel(ctx, road.name, xOf(road.cx + road.rx * 0.84), yOf(road.cy - road.ry * 0.55), {
      size: 9,
      weight: 700,
      color: road.color,
    })
  })

  const roads = [
    { name: '延安高架', points: [[0.08, 0.55], [0.29, 0.51], [0.48, 0.48], [0.67, 0.43], [0.92, 0.35]], color: 'rgba(255, 203, 104, 0.78)', width: 3.2 },
    { name: '南北高架', points: [[0.45, 0.08], [0.49, 0.27], [0.5, 0.47], [0.45, 0.69], [0.42, 0.92]], color: 'rgba(255, 203, 104, 0.62)', width: 2.8 },
    { name: '沪闵高架', points: [[0.2, 0.92], [0.32, 0.75], [0.42, 0.63], [0.5, 0.52]], color: 'rgba(220, 232, 210, 0.48)', width: 2.4 },
    { name: '龙东大道', points: [[0.56, 0.63], [0.7, 0.64], [0.84, 0.59], [0.96, 0.55]], color: 'rgba(220, 232, 210, 0.4)', width: 2.2 },
    { name: '杨浦大桥', points: [[0.53, 0.27], [0.6, 0.25], [0.7, 0.24]], color: 'rgba(220, 232, 210, 0.42)', width: 2.2 },
  ]
  roads.forEach(road => {
    ctx.save()
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.strokeStyle = 'rgba(3, 10, 16, 0.68)'
    ctx.lineWidth = (road.width + 2.2) * roadScale
    drawMapPolyline(ctx, road.points, xOf, yOf)
    ctx.strokeStyle = road.color
    ctx.lineWidth = road.width * roadScale
    drawMapPolyline(ctx, road.points, xOf, yOf)
    ctx.restore()
  })

  const poiStyle = {
    hub: '#ffd06b',
    civic: '#d7ecff',
    tech: '#43d184',
    river: '#80cfff',
  }
  const pois = [
    { name: '人民广场', x: 0.49, y: 0.45, type: 'civic' },
    { name: '陆家嘴', x: 0.54, y: 0.39, type: 'hub' },
    { name: '外滩', x: 0.51, y: 0.39, type: 'river' },
    { name: '徐家汇', x: 0.39, y: 0.58, type: 'civic' },
    { name: '五角场', x: 0.58, y: 0.22, type: 'civic' },
    { name: '张江', x: 0.74, y: 0.53, type: 'tech' },
    { name: '虹桥枢纽', x: 0.18, y: 0.61, type: 'hub' },
  ]
  pois.forEach(poi => {
    const x = xOf(poi.x)
    const y = yOf(poi.y)
    ctx.save()
    ctx.fillStyle = poiStyle[poi.type] || '#d7ecff'
    ctx.strokeStyle = 'rgba(3, 10, 16, 0.72)'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(x, y, poi.type === 'hub' ? 3.8 : 3, 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()
    ctx.restore()
    drawMapLabel(ctx, poi.name, x + 14, y - 7, {
      align: 'left',
      size: 9,
      weight: 700,
      color: 'rgba(220, 236, 246, 0.76)',
    })
  })

  const cityPois = [
    { name: '人民广场', position: [121.475, 31.232], type: 'civic' },
    { name: '陆家嘴', position: [121.501, 31.238], type: 'hub' },
    { name: '虹桥枢纽', position: [121.327, 31.194], type: 'hub' },
    { name: '浦东机场', position: [121.805, 31.144], type: 'hub' },
    { name: '临港', position: [121.91, 30.9], type: 'tech' },
    { name: '奉贤南桥', position: [121.474, 30.918], type: 'civic' },
    { name: '金山卫', position: [121.343, 30.728], type: 'civic' },
    { name: '松江大学城', position: [121.214, 31.057], type: 'tech' },
    { name: '嘉定安亭', position: [121.166, 31.29], type: 'tech' },
    { name: '崇明陈家镇', position: [121.8, 31.5], type: 'civic' },
  ]
  cityPois.forEach(poi => {
    const projected = normalizedShanghaiPoint(poi.position)
    const x = xOf(projected.x)
    const y = yOf(projected.y)
    ctx.save()
    ctx.fillStyle = poiStyle[poi.type] || '#d7ecff'
    ctx.strokeStyle = 'rgba(3, 10, 16, 0.72)'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(x, y, poi.type === 'hub' ? 3.8 : 2.8, 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()
    ctx.restore()
    if (zoom > 0.95) {
      drawMapLabel(ctx, poi.name, x + 11, y - 6, {
        align: 'left',
        size: 8,
        weight: 800,
        color: 'rgba(220, 236, 246, 0.72)',
      })
    }
  })

  ctx.save()
  ctx.fillStyle = 'rgba(3, 10, 16, 0.66)'
  ctx.strokeStyle = 'rgba(77, 163, 255, 0.26)'
  drawRoundedRect(ctx, 10, 10, 228, 44, 8)
  ctx.fill()
  ctx.stroke()
  drawMapLabel(ctx, 'SHANGHAI OFFLINE VECTOR MAP', 20, 24, {
    align: 'left',
    size: 10,
    weight: 800,
    color: '#d7ecff',
  })
  drawMapLabel(ctx, '黄浦江 / 路网 / 应急点位本地渲染', 20, 41, {
    align: 'left',
    size: 10,
    weight: 700,
    color: 'rgba(180, 214, 230, 0.82)',
  })
  ctx.strokeStyle = 'rgba(216, 239, 255, 0.68)'
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(18, height - 28)
  ctx.lineTo(82, height - 28)
  ctx.stroke()
  drawMapLabel(ctx, '5 km', 50, height - 40, {
    size: 9,
    weight: 800,
    color: 'rgba(216, 239, 255, 0.82)',
  })
  ctx.fillStyle = 'rgba(216, 239, 255, 0.82)'
  ctx.beginPath()
  ctx.moveTo(width - 32, 24)
  ctx.lineTo(width - 40, 48)
  ctx.lineTo(width - 32, 42)
  ctx.lineTo(width - 24, 48)
  ctx.closePath()
  ctx.fill()
  drawMapLabel(ctx, 'N', width - 32, 60, {
    size: 10,
    weight: 900,
    color: '#d7ecff',
  })
  ctx.restore()

  const contacts = (incidents.value.length ? incidents.value : events.value).slice(0, 12)
  if (!contacts.length) {
    drawMapLabel(ctx, '等待 SDR 识别点位进入地图', width / 2, height / 2, {
      size: 14,
      weight: 800,
      color: 'rgba(216, 239, 255, 0.72)',
    })
    return
  }

  contacts.forEach((item, index) => {
    const point = mapPointFor(item, index)
    const x = xOf(point.x)
    const y = yOf(point.y)
    const severity = item.severity || 'normal'
    const pulse = 0.5 + Math.sin(timestamp / 360 + index * 0.7) * 0.5
    const color = severityColor(severity, 0.95)
    ctx.save()
    ctx.fillStyle = severityColor(severity, 0.16)
    ctx.beginPath()
    ctx.arc(x, y, 13 + pulse * 14, 0, Math.PI * 2)
    ctx.fill()
    ctx.shadowBlur = 18
    ctx.shadowColor = severityColor(severity, 0.58)
    ctx.fillStyle = color
    ctx.strokeStyle = '#f3f7ff'
    ctx.lineWidth = 1.4
    ctx.beginPath()
    ctx.moveTo(x, y + 14)
    ctx.bezierCurveTo(x - 12, y + 2, x - 9, y - 13, x, y - 13)
    ctx.bezierCurveTo(x + 9, y - 13, x + 12, y + 2, x, y + 14)
    ctx.fill()
    ctx.stroke()
    ctx.shadowBlur = 0
    ctx.fillStyle = 'rgba(255, 255, 255, 0.94)'
    ctx.beginPath()
    ctx.arc(x, y - 3, 3.6, 0, Math.PI * 2)
    ctx.fill()

    const label = String(displayMapLocation(item) || item.event_type || point.regionName || '事件').slice(0, 12)
    ctx.font = '700 11px "Microsoft YaHei", Arial, sans-serif'
    const labelWidth = Math.min(138, Math.max(54, ctx.measureText(label).width + 18))
    const labelX = x + labelWidth + 16 > width ? x - labelWidth - 12 : x + 12
    const labelY = y - 25 - (index % 2) * 10
    ctx.fillStyle = 'rgba(3, 10, 16, 0.82)'
    ctx.strokeStyle = severityColor(severity, 0.72)
    ctx.lineWidth = 1
    drawRoundedRect(ctx, labelX, labelY, labelWidth, 22, 6)
    ctx.fill()
    ctx.stroke()
    drawMapLabel(ctx, label, labelX + 9, labelY + 11, {
      align: 'left',
      size: 11,
      weight: 800,
      color: '#f3f7ff',
      halo: null,
    })
    ctx.restore()
  })
}

function drawRadar(timestamp) {
  const canvas = radarCanvas.value
  if (!canvas) return
  const fitted = fitCanvas(canvas)
  if (!fitted) return
  const { ctx, width, height } = fitted
  const size = Math.min(width, height)
  const cx = width / 2
  const cy = height / 2
  const radius = size / 2 - 7
  const sweep = (timestamp / 950) % (Math.PI * 2)

  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = '#04100d'
  ctx.fillRect(0, 0, width, height)

  ctx.strokeStyle = 'rgba(49, 210, 124, 0.22)'
  ctx.lineWidth = 1
  for (let ring = 0.34; ring <= 1; ring += 0.33) {
    ctx.beginPath()
    ctx.arc(cx, cy, radius * ring, 0, Math.PI * 2)
    ctx.stroke()
  }
  for (let i = 0; i < 8; i += 1) {
    const angle = (Math.PI * 2 / 8) * i
    ctx.beginPath()
    ctx.moveTo(cx, cy)
    ctx.lineTo(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius)
    ctx.stroke()
  }

  const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius)
  gradient.addColorStop(0, 'rgba(49, 210, 124, 0.24)')
  gradient.addColorStop(1, 'rgba(49, 210, 124, 0)')
  ctx.fillStyle = gradient
  ctx.beginPath()
  ctx.moveTo(cx, cy)
  ctx.arc(cx, cy, radius, sweep - 0.38, sweep, false)
  ctx.closePath()
  ctx.fill()

  ctx.strokeStyle = '#31d27c'
  ctx.beginPath()
  ctx.moveTo(cx, cy)
  ctx.lineTo(cx + Math.cos(sweep) * radius, cy + Math.sin(sweep) * radius)
  ctx.stroke()

  const contacts = incidents.value.length ? incidents.value : events.value.slice(0, 6)
  contacts.slice(0, 8).forEach((item, index) => {
    const angle = index * 1.37 + 0.25
    const distance = radius * (0.28 + (index % 4) * 0.17)
    const pulse = 0.65 + Math.sin(timestamp / 260 + index) * 0.35
    const x = cx + Math.cos(angle) * distance
    const y = cy + Math.sin(angle) * distance
    ctx.fillStyle = item.severity === 'critical'
      ? '#ff4d4d'
      : item.severity === 'urgent'
        ? '#ffad3b'
        : '#4da3ff'
    ctx.globalAlpha = 0.58 + pulse * 0.35
    ctx.beginPath()
    ctx.arc(x, y, 3.2 + pulse * 2.2, 0, Math.PI * 2)
    ctx.fill()
  })
  ctx.globalAlpha = 1
}

function animateRadio(timestamp = 0) {
  if (offlineMapActive.value) drawOfflineMap(timestamp)
  drawSpectrum()
  drawHeatmap()
  drawWaterfall()
  drawRadar(timestamp)
  radioFrame = requestAnimationFrame(animateRadio)
}

function connectWS() {
  if (ws) {
    try { ws.close() } catch {}
  }

  ws = new WebSocket(WS_URL)
  ws.onopen = () => {
    wsConnected.value = true
    clearTimeout(reconnectTimer)
    requestStats()
    requestRadioSnapshot()
    requestTranscripts()
  }
  ws.onmessage = message => {
    const payload = JSON.parse(message.data)
    if (payload.type === 'event') {
      handleEvent(payload.data)
    } else if (payload.type === 'stats') {
      pipelineStats.value = payload.data
    } else if (payload.type === 'incident_status') {
      upsertIncident(payload.data)
    } else if (payload.type === 'radio_telemetry') {
      applyRadioSnapshot(payload.data)
    } else if (payload.type === 'transcript') {
      handleTranscript(payload.data)
    } else if (payload.type === 'transcripts') {
      transcripts.value = (payload.data || []).slice(0, 80)
    } else if (payload.type === 'ping') {
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  }
  ws.onclose = () => {
    wsConnected.value = false
    reconnectTimer = setTimeout(connectWS, 3000)
  }
  ws.onerror = () => {
    wsConnected.value = false
  }
}

function handleEvent(event) {
  if (!event?.id) return
  if (events.value.some(item => item.id === event.id)) return
  events.value.unshift(event)
  events.value = events.value.slice(0, 60)
  tickerItems.value.unshift({
    id: event.id,
    severity: event.severity,
    text: `${event.event_type} / ${event.location} / ${event.raw_text.slice(0, 34)}`,
  })
  tickerItems.value = tickerItems.value.slice(0, 20)
  fetchIncidents()
  nextTick(() => {
    if (eventList.value) eventList.value.scrollTop = 0
  })
}

function upsertIncident(incident) {
  const index = incidents.value.findIndex(item => item.incident_id === incident.incident_id)
  if (index >= 0) {
    incidents.value.splice(index, 1, incident)
  } else {
    incidents.value.unshift(incident)
  }
  updateOnlineMapMarkers()
}

function handleTranscript(item) {
  if (!item?.id) return
  const normalized = {
    ...item,
    asr_latency: Number(item.asr_latency || 0),
    pipeline_latency: Number(item.pipeline_latency || 0),
  }
  const index = transcripts.value.findIndex(entry => entry.id === normalized.id)
  if (index >= 0) {
    transcripts.value.splice(index, 1, normalized)
  } else {
    transcripts.value.unshift(normalized)
  }
  transcripts.value = transcripts.value.slice(0, 80)
}

function addLocalTranscript(text, source = 'browser') {
  const trimmed = String(text || '').trim()
  if (!trimmed) return null
  const item = {
    id: `LOCAL-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    sequence: transcripts.value.length + 1,
    timestamp: new Date().toISOString(),
    text: trimmed,
    source,
    usable: true,
    review_status: 'ready',
    asr_latency: 0,
    pipeline_latency: 0,
    language: 'zh-CN',
    language_probability: 1,
  }
  handleTranscript(item)
  return item
}

function transcriptSourceLabel(source) {
  return {
    radio: 'SDR',
    voice: '麦克风',
    browser: '浏览器',
    manual: '手动',
    review: '复核',
  }[source] || String(source || '语音')
}

function transcriptStatusLabel(item) {
  if (!item.usable) return '需人工听辨'
  if (item.review_status === 'injected') return '已入屏'
  return '待复核'
}

async function useTranscript(item, inject = false) {
  if (!item?.usable) return
  manualText.value = item.text
  if (inject) {
    await submitManual('review')
    item.review_status = 'injected'
  }
}

function applyRadioSnapshot(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.channels)) return
  radioSnapshot.value = snapshot
  const activeId = snapshot.active_channel_id || snapshot.channels[0]?.id || ''
  if (!selectedChannelId.value || !snapshot.channels.some(channel => channel.id === selectedChannelId.value)) {
    selectedChannelId.value = activeId
  }

  const active = snapshot.channels.find(channel => channel.id === selectedChannelId.value)
    || snapshot.channels.find(channel => channel.id === activeId)
  if (active && Array.isArray(active.spectrum) && active.spectrum.length > 1) {
    waterfallRows.value.unshift([...active.spectrum])
    waterfallRows.value = waterfallRows.value.slice(0, 48)
  }
}

async function fetchIncidents() {
  const response = await fetch('/api/incidents')
  const payload = await response.json()
  incidents.value = payload.incidents || []
  updateOnlineMapMarkers()
}

async function fetchEventHistory() {
  const response = await fetch('/api/events/history?limit=60')
  const payload = await response.json()
  events.value = payload.events || []
  tickerItems.value = events.value.slice(0, 20).map(event => ({
    id: event.id,
    severity: event.severity,
    text: `${event.event_type} / ${event.location} / ${String(event.raw_text || '').slice(0, 34)}`,
  }))
}

async function fetchRadioSnapshot() {
  const response = await fetch('/api/radio/snapshot')
  const payload = await response.json()
  applyRadioSnapshot(payload)
}

async function fetchTranscripts() {
  const response = await fetch('/api/asr/transcripts')
  const payload = await response.json()
  transcripts.value = (payload.transcripts || []).slice(0, 80)
}

async function requestStats() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'get_stats' }))
  }
}

async function requestRadioSnapshot() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'get_radio' }))
  } else {
    await fetchRadioSnapshot()
  }
}

async function requestTranscripts() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'get_transcripts' }))
  } else {
    await fetchTranscripts()
  }
}

function adminHeaders(headers = {}) {
  return ADMIN_TOKEN ? { ...headers, 'X-Admin-Token': ADMIN_TOKEN } : headers
}

function buildVoiceRecognition() {
  if (!speechSupported.value) return null
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
  const recognition = new Recognition()
  recognition.lang = 'zh-CN'
  recognition.continuous = false
  recognition.interimResults = true
  recognition.maxAlternatives = 1

  recognition.onstart = () => {
    voiceListening.value = true
    voiceSubmitted = false
    voiceStatusType.value = 'active'
    voiceStatus.value = '正在收音：请说一条应急通联，例如“徐汇龙华路有人被困”'
  }

  recognition.onresult = event => {
    let finalText = ''
    let interimText = ''
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const text = event.results[index][0]?.transcript || ''
      if (event.results[index].isFinal) finalText += text
      else interimText += text
    }
    const heardText = (finalText || interimText).trim()
    if (heardText) {
      manualText.value = heardText
      voiceStatusType.value = 'active'
      voiceStatus.value = finalText
        ? `已识别：${finalText.trim()}，正在注入大屏…`
        : `正在识别：${interimText.trim()}`
    }
    if (finalText.trim() && !voiceSubmitted) {
      voiceSubmitted = true
      manualText.value = finalText.trim()
      addLocalTranscript(finalText.trim(), 'browser')
      void submitManual('voice')
      try { recognition.stop() } catch {}
    }
  }

  recognition.onerror = event => {
    voiceListening.value = false
    voiceStatusType.value = 'error'
    const reason = event.error === 'not-allowed'
      ? '浏览器没有麦克风权限，请允许麦克风后再点语音'
      : `语音识别中断：${event.error || '未知错误'}`
    voiceStatus.value = reason
  }

  recognition.onend = () => {
    voiceListening.value = false
    if (voiceStatusType.value !== 'error' && !voiceSubmitted) {
      voiceStatusType.value = 'idle'
      voiceStatus.value = manualText.value
        ? '已填入识别文本，可点“注入”进入大屏'
        : '未收到可用语音，请靠近麦克风再试一次'
    }
  }

  return recognition
}

function toggleVoiceInput() {
  if (voiceListening.value) {
    try { voiceRecognition?.stop() } catch {}
    voiceListening.value = false
    return
  }
  if (!speechSupported.value) {
    voiceStatusType.value = 'error'
    voiceStatus.value = '当前浏览器不支持 Web Speech 语音识别，请用 Chrome 或直接手动输入'
    return
  }
  voiceRecognition = buildVoiceRecognition()
  try {
    voiceRecognition.start()
  } catch (error) {
    voiceListening.value = false
    voiceStatusType.value = 'error'
    voiceStatus.value = `语音启动失败：${error?.message || error}`
  }
}

async function startDemo() {
  await fetch('/api/demo/start', {
    method: 'POST',
    headers: adminHeaders(),
  })
}

async function submitManual(source = 'manual') {
  if (typeof source !== 'string') source = 'manual'
  const text = manualText.value.trim()
  if (!text) return
  manualText.value = ''
  voiceStatusType.value = source === 'voice' ? 'active' : voiceStatusType.value
  const response = await fetch('/api/events/manual', {
    method: 'POST',
    headers: adminHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text, source }),
  })
  if (!response.ok) {
    voiceStatusType.value = 'error'
    voiceStatus.value = `注入失败：HTTP ${response.status}`
    manualText.value = text
    return
  }
  const payload = await response.json()
  if (payload.event) handleEvent(payload.event)
  requestStats()
  fetchIncidents()
  if (source === 'voice') {
    voiceStatusType.value = 'idle'
    voiceStatus.value = '语音已注入大屏，可继续说下一条'
  }
}

async function setIncidentStatus(incidentId, status) {
  await fetch(`/api/incidents/${incidentId}/status`, {
    method: 'POST',
    headers: adminHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ status }),
  })
}

onMounted(() => {
  clockTimer = setInterval(() => {
    currentTime.value = new Date().toLocaleString('zh-CN', {
      hour12: false,
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  }, 1000)
  connectWS()
  fetchEventHistory()
  fetchIncidents()
  fetchRadioSnapshot()
  fetchTranscripts()
  nextTick(initOnlineMap)
  radioFrame = requestAnimationFrame(animateRadio)
  statsTimer = setInterval(() => {
    requestStats()
    requestRadioSnapshot()
    requestTranscripts()
    fetchIncidents()
  }, 8000)
})

onUnmounted(() => {
  clearInterval(clockTimer)
  clearInterval(statsTimer)
  clearTimeout(reconnectTimer)
  if (radioFrame) cancelAnimationFrame(radioFrame)
  if (ws) ws.close()
  try { voiceRecognition?.stop() } catch {}
  clearOnlineMarkers()
  if (onlineMapProvider === 'amap' && onlineMap?.destroy) onlineMap.destroy()
  if (onlineMapProvider === 'baidu' && onlineMap?.destroy) onlineMap.destroy()
})
</script>

<style scoped>
.command-screen {
  width: 100vw;
  height: 100vh;
  background: #05070a;
  color: #f3f7ff;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
}

.topbar {
  min-height: 76px;
  padding: 14px 28px;
  border-bottom: 1px solid #18324a;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand h1 {
  margin: 0;
  font-size: 32px;
  line-height: 1.1;
  color: #ffffff;
}

.brand p {
  margin: 5px 0 0;
  font-size: 15px;
  color: #87a9c8;
}

.beacon {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #c63737;
}

.beacon.online {
  background: #31d27c;
  box-shadow: 0 0 14px rgba(49, 210, 124, 0.8);
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 18px;
}

.ghost-btn,
.manual-input button,
.status-actions button {
  border: 1px solid #2d5576;
  background: #0c1a25;
  color: #d7ecff;
  border-radius: 6px;
  font-weight: 700;
  cursor: pointer;
}

.ghost-btn {
  height: 42px;
  padding: 0 18px;
  font-size: 16px;
}

.clock {
  color: #a8c7de;
  font-size: 22px;
  font-variant-numeric: tabular-nums;
}

.metrics-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: 1px;
  background: #18324a;
  border-bottom: 1px solid #18324a;
}

.metric {
  background: #07101a;
  padding: 14px 22px;
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.metric-value {
  font-size: 34px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.metric-label {
  color: #8fb0ca;
  font-size: 16px;
}

.metric.critical .metric-value { color: #ff4d4d; }
.metric.urgent .metric-value { color: #ffad3b; }
.metric.normal .metric-value { color: #4da3ff; }

.workspace {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(540px, 1fr);
  gap: 18px;
  padding: 18px 24px 14px;
}

.feed-panel,
.ops-panel {
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.ops-panel {
  gap: 16px;
  overflow-y: auto;
}

.section-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.section-heading span {
  font-size: 22px;
  font-weight: 800;
  color: #e8f5ff;
}

.section-heading small {
  color: #7897b0;
  font-size: 13px;
  text-align: right;
}

.manual-input {
  display: grid;
  grid-template-columns: 1fr 86px 74px;
  gap: 10px;
  margin-bottom: 8px;
}

.manual-input input {
  height: 44px;
  border: 1px solid #1e3d58;
  background: #07111a;
  color: #ffffff;
  border-radius: 6px;
  padding: 0 14px;
  font-size: 16px;
}

.manual-input button {
  font-size: 16px;
}

.manual-input .voice-btn {
  border-color: rgba(49, 210, 124, 0.5);
  color: #baf8d4;
}

.manual-input .voice-btn.listening {
  color: #03110a;
  background: #31d27c;
  box-shadow: 0 0 0 4px rgba(49, 210, 124, 0.16), 0 0 20px rgba(49, 210, 124, 0.28);
}

.manual-input .voice-btn:disabled {
  cursor: not-allowed;
  color: #7897b0;
  background: #07111a;
  border-color: #1e3d58;
  opacity: 0.62;
}

.voice-status {
  min-height: 22px;
  margin: 0 0 10px;
  color: #8fb2cb;
  font-size: 13px;
}

.voice-status.active {
  color: #baf8d4;
}

.voice-status.error {
  color: #ffad3b;
}

.transcript-review {
  border: 1px solid #1e3d58;
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(7, 17, 26, 0.98), rgba(5, 12, 19, 0.94));
  margin-bottom: 12px;
  overflow: hidden;
}

.review-heading {
  min-height: 38px;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(30, 61, 88, 0.72);
}

.review-heading span {
  color: #e8f5ff;
  font-size: 15px;
  font-weight: 900;
}

.review-heading small {
  color: #7897b0;
  font-size: 12px;
}

.transcript-list {
  max-height: 188px;
  overflow-y: auto;
  padding: 8px;
}

.transcript-item {
  border-left: 3px solid #31d27c;
  background: rgba(8, 19, 29, 0.96);
  border-radius: 7px;
  padding: 8px 9px;
  margin-bottom: 8px;
}

.transcript-item.muted {
  border-left-color: #ffad3b;
  opacity: 0.74;
}

.transcript-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #7897b0;
  font-size: 11px;
}

.transcript-meta strong {
  color: #31d27c;
  font-style: normal;
}

.transcript-meta em {
  font-style: normal;
}

.transcript-item p {
  margin: 6px 0;
  color: #f3f7ff;
  font-size: 14px;
  line-height: 1.45;
}

.transcript-actions {
  display: flex;
  align-items: center;
  gap: 7px;
}

.transcript-actions span {
  flex: 1;
  color: #8fb2cb;
  font-size: 12px;
}

.transcript-actions button {
  height: 25px;
  min-width: 46px;
  border: 1px solid #245274;
  border-radius: 4px;
  background: #07111a;
  color: #d7ecff;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.transcript-actions button:last-child {
  border-color: rgba(49, 210, 124, 0.5);
  color: #baf8d4;
}

.transcript-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

.empty-transcript {
  color: #7897b0;
  font-size: 13px;
  text-align: center;
  padding: 20px 8px;
}

.event-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 8px;
}

.event-row {
  border-left: 5px solid #4da3ff;
  background: #08131d;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px;
  gap: 14px;
}

.event-row.critical { border-left-color: #ff4d4d; background: #1d0b0d; }
.event-row.urgent { border-left-color: #ffad3b; background: #1d1408; }
.event-row.minor { border-left-color: #43d184; }

.event-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 9px;
}

.severity,
.status-pill,
.review-tag {
  border-radius: 5px;
  padding: 4px 9px;
  font-size: 13px;
  font-weight: 800;
}

.severity.critical { background: #b91c1c; color: white; }
.severity.urgent { background: #b76700; color: white; }
.severity.normal { background: #155a9c; color: white; }
.severity.minor { background: #147a45; color: white; }

.event-type {
  color: #bfe2ff;
  font-size: 17px;
  font-weight: 700;
}

.event-time {
  margin-left: auto;
  color: #7897b0;
  font-size: 14px;
}

.event-text {
  margin: 0 0 10px;
  font-size: 23px;
  line-height: 1.45;
  font-weight: 800;
  word-break: break-word;
}

.event-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.event-facts span,
.keyword-line span,
.resource-line span {
  background: #102438;
  color: #bad7ed;
  border-radius: 5px;
  padding: 4px 8px;
  font-size: 13px;
}

.event-aside {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}

.status-pill.new { background: #2b3d4e; color: #d8e9f8; }
.status-pill.confirmed { background: #124878; color: #cce9ff; }
.status-pill.dispatched { background: #6b4a00; color: #ffe2a3; }
.status-pill.handling { background: #704214; color: #ffd7a8; }
.status-pill.resolved { background: #145c38; color: #c9f7df; }
.review-tag { background: #54242b; color: #ffc6cf; }

.incident-panel,
.map-panel,
.radio-panel,
.decision-panel {
  flex: 0 0 auto;
  background: #08131d;
  border: 1px solid #18324a;
  border-radius: 8px;
  padding: 14px;
}

/* Keep live receiver evidence in the first visible operator viewport. */
.radio-panel {
  order: -2;
}

.map-panel {
  order: -3;
}

.incident-panel {
  order: -1;
}

.incident-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.incident-item {
  border-top: 1px solid #18324a;
  padding-top: 12px;
}

.incident-item:first-child {
  border-top: 0;
  padding-top: 0;
}

.incident-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.incident-title strong {
  line-height: 1.35;
}

.severity-dot {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: #4da3ff;
  flex: 0 0 auto;
}

.severity-dot.critical { background: #ff4d4d; }
.severity-dot.urgent { background: #ffad3b; }
.severity-dot.minor { background: #43d184; }

.incident-meta,
.resource-line,
.keyword-line {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  color: #8eadc7;
  font-size: 13px;
  margin-bottom: 9px;
}

.status-actions {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 5px;
}

.status-actions button {
  min-height: 32px;
  font-size: 12px;
  padding: 0 4px;
}

.status-actions button.active {
  background: #1e6ea9;
  color: white;
}

.region-map {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 5px;
}

.offline-map-frame {
  position: relative;
  height: clamp(390px, 45vh, 540px);
  border: 1px solid #203f5a;
  background: #061019;
  border-radius: 7px;
  overflow: hidden;
  margin-bottom: 9px;
}

.offline-map-frame.online {
  background: #061019;
}

.offline-map-frame.dragging {
  cursor: grabbing;
}

.offline-map-frame::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.04), transparent 22%, transparent 78%, rgba(255, 255, 255, 0.03)),
    radial-gradient(circle at 54% 48%, rgba(77, 163, 255, 0.08), transparent 42%);
}

.online-map {
  position: absolute;
  inset: 0;
  z-index: 1;
}

.offline-map-canvas {
  position: absolute;
  inset: 0;
  z-index: 2;
  width: 100%;
  height: 100%;
  display: block;
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.map-switch {
  position: absolute;
  z-index: 5;
  left: 10px;
  top: 10px;
  display: flex;
  overflow: hidden;
  border: 1px solid rgba(77, 163, 255, 0.28);
  border-radius: 7px;
  background: rgba(3, 10, 16, 0.76);
}

.map-switch button {
  height: 29px;
  min-width: 44px;
  border: 0;
  border-right: 1px solid rgba(77, 163, 255, 0.2);
  background: transparent;
  color: #8fb2cb;
  font-size: 12px;
  font-weight: 900;
  cursor: pointer;
}

.map-switch button:last-child {
  border-right: 0;
}

.map-switch button.active {
  color: #03110a;
  background: #31d27c;
}

.map-loading {
  position: absolute;
  inset: 0;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  text-align: center;
  color: #d7ecff;
  background: rgba(3, 10, 16, 0.72);
  font-size: 13px;
}

.map-controls {
  position: absolute;
  z-index: 4;
  top: 10px;
  right: 10px;
  display: flex;
  overflow: hidden;
  border: 1px solid rgba(77, 163, 255, 0.28);
  border-radius: 7px;
  background: rgba(3, 10, 16, 0.76);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.24);
}

.map-controls button {
  min-width: 34px;
  height: 29px;
  border: 0;
  border-right: 1px solid rgba(77, 163, 255, 0.2);
  background: transparent;
  color: #d7ecff;
  font-size: 12px;
  font-weight: 900;
  cursor: pointer;
}

.map-controls button:last-child {
  min-width: 42px;
  border-right: 0;
  font-weight: 800;
}

.map-controls button:hover {
  background: rgba(77, 163, 255, 0.18);
}

.map-hint {
  position: absolute;
  z-index: 4;
  left: 12px;
  bottom: 10px;
  color: rgba(216, 239, 255, 0.78);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.02em;
  background: rgba(3, 10, 16, 0.68);
  border: 1px solid rgba(77, 163, 255, 0.22);
  border-radius: 999px;
  padding: 4px 8px;
}

.map-meta {
  position: absolute;
  z-index: 4;
  right: 10px;
  bottom: 9px;
  display: flex;
  align-items: center;
  gap: 7px;
  color: #d7ecff;
  font-size: 10px;
  font-weight: 800;
  background: rgba(3, 10, 16, 0.76);
  border: 1px solid rgba(77, 163, 255, 0.32);
  border-radius: 4px;
  padding: 4px 6px;
}

.map-meta span {
  color: #31d27c;
}

.map-legend {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 8px;
}

.map-legend span {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: #a8c7de;
  font-size: 12px;
  background: #07111a;
  border: 1px solid #203f5a;
  border-radius: 5px;
  padding: 5px 4px;
  white-space: nowrap;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.legend-dot.critical { background: #ff4d4d; }
.legend-dot.urgent { background: #ffad3b; }
.legend-dot.normal { background: #4da3ff; }
.legend-dot.minor { background: #43d184; }

.region {
  min-height: 35px;
  border: 1px solid #203f5a;
  background: #07111a;
  border-radius: 5px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 2px;
}

.region span {
  color: #a8c7de;
  font-size: 10px;
}

.region strong {
  font-size: 16px;
}

.region.critical { border-color: #ff4d4d; background: #210d0f; }
.region.urgent { border-color: #ffad3b; background: #211608; }
.region.normal { border-color: #4da3ff; }

.radio-panel {
  padding: 0;
  overflow: hidden;
  background: #07110f;
  border-color: #27443a;
}

.analyzer-shell {
  display: grid;
  grid-template-columns: 12px minmax(0, 1fr) 12px;
  gap: 8px;
  padding: 10px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.08), transparent 18%),
    linear-gradient(135deg, #203a35 0%, #0d211d 48%, #071412 100%);
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(198, 239, 217, 0.1), inset 0 -18px 34px rgba(0, 0, 0, 0.28);
}

.analyzer-grip {
  border-radius: 999px;
  background: linear-gradient(90deg, #08110f 0%, #38544d 45%, #0b1715 100%);
  box-shadow: inset 0 0 12px rgba(0, 0, 0, 0.72);
}

.analyzer-face {
  min-width: 0;
  padding: 9px;
  border-radius: 7px;
  background: #172d27;
  border: 1px solid #35574d;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 10px 24px rgba(0, 0, 0, 0.3);
}

.analyzer-topline {
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #dcefe8;
  margin-bottom: 8px;
}

.analyzer-topline div {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.analyzer-topline strong {
  font-size: 15px;
  white-space: nowrap;
}

.analyzer-topline small {
  color: #a7c9bd;
  font-size: 12px;
  text-align: right;
  white-space: nowrap;
}

.analyzer-brand {
  color: #ff513f;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0;
}

.channel-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 8px;
}

.channel-tab {
  min-width: 0;
  min-height: 46px;
  border: 1px solid #35574d;
  border-radius: 4px;
  background: #10251f;
  color: #dcefe8;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-rows: auto auto;
  gap: 2px 6px;
  align-items: center;
  padding: 6px 7px;
  cursor: pointer;
  text-align: left;
}

.channel-tab span,
.channel-tab strong,
.channel-tab small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.channel-tab span {
  font-size: 11px;
  font-weight: 800;
}

.channel-tab strong {
  color: #f3f7ff;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.channel-tab small {
  grid-column: 1 / -1;
  color: #91afa4;
  font-size: 9px;
}

.channel-tab.active {
  border-color: #dbff77;
  box-shadow: 0 0 0 1px rgba(219, 255, 119, 0.16), 0 0 12px rgba(49, 210, 124, 0.12);
}

.channel-tab.receiving {
  background: #143221;
}

.channel-tab.receiving small {
  color: #7cff9f;
}

.channel-tab.stale {
  border-color: #7d3939;
  background: #241515;
}

.channel-tab.stale small {
  color: #ff9a9a;
}

.analyzer-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 98px;
  gap: 9px;
}

.analyzer-screen {
  min-width: 0;
  padding: 7px;
  border-radius: 5px;
  background: #010202;
  border: 2px solid #0a0d0d;
  box-shadow: inset 0 0 0 1px #38413d, 0 0 18px rgba(49, 210, 124, 0.08);
}

.screen-toolbar {
  height: 24px;
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 8px;
  align-items: center;
  padding: 0 7px;
  margin-bottom: 6px;
  color: #c4d6d0;
  background: #080b0b;
  border: 1px solid #222c2a;
  border-radius: 3px;
  font-size: 10px;
  white-space: nowrap;
}

.screen-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.18fr) minmax(104px, 0.82fr);
  gap: 6px;
}

.trace-pane,
.heat-pane,
.waterfall-pane,
.radar-pane {
  min-width: 0;
  background: #000;
  border: 1px solid #293530;
  border-radius: 3px;
  padding: 6px;
}

.stacked-pane {
  min-width: 0;
  display: grid;
  grid-template-rows: 1fr 1fr;
  gap: 6px;
}

.scope-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.scope-label span {
  color: #dcefe8;
  font-size: 11px;
  font-weight: 800;
  white-space: nowrap;
}

.scope-label small {
  color: #95afa6;
  font-size: 9px;
  white-space: nowrap;
}

.spectrum-canvas,
.heatmap-canvas,
.waterfall-canvas,
.radar-canvas {
  width: 100%;
  display: block;
  border-radius: 2px;
}

.spectrum-canvas {
  height: 210px;
}

.heatmap-canvas,
.waterfall-canvas {
  height: 94px;
}

.screen-readouts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) 1.1fr;
  gap: 6px;
  margin-top: 6px;
}

.screen-readouts > div {
  min-height: 38px;
  padding: 5px 6px;
  background: #06100d;
  border: 1px solid #233c35;
  border-radius: 3px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
}

.screen-readouts span {
  color: #92aca2;
  font-size: 10px;
}

.screen-readouts strong {
  color: #f3f7ff;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.signal-bars {
  padding: 5px 6px 4px;
  display: flex;
  flex-direction: row;
  align-items: flex-end;
  gap: 4px;
}

.signal-bars span {
  width: 100%;
  border-radius: 2px 2px 0 0;
  background: #18342f;
  opacity: 0.72;
  transition: background 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
}

.signal-bars span.active {
  background: linear-gradient(180deg, #dbff77 0%, #31d27c 100%);
  box-shadow: 0 0 9px rgba(220, 255, 119, 0.46);
  opacity: 1;
}

.analyzer-controls {
  min-width: 0;
  padding: 6px;
  border-radius: 5px;
  background: linear-gradient(180deg, #223f38 0%, #11271f 100%);
  border: 1px solid #385c50;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rx-indicator {
  height: 24px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: #cfe6dc;
  font-size: 11px;
}

.rx-indicator span {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #7d2527;
}

.rx-indicator span.online {
  background: #7cff9f;
  box-shadow: 0 0 11px rgba(124, 255, 159, 0.8);
}

.keypad {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 5px;
}

.keypad button {
  min-width: 0;
  height: 24px;
  border: 1px solid #6b8178;
  border-radius: 4px;
  background: linear-gradient(180deg, #587068 0%, #263a34 100%);
  color: #ecfff8;
  font-size: 9px;
  font-weight: 800;
  cursor: default;
  padding: 0;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.16), 0 1px 2px rgba(0, 0, 0, 0.32);
}

.tuning-dial {
  width: 66px;
  height: 66px;
  margin: 0 auto;
  border-radius: 50%;
  background:
    radial-gradient(circle at 50% 50%, #0d1916 0 22%, #506861 24% 27%, #172821 29% 100%);
  border: 1px solid #698a80;
  box-shadow: inset 0 0 12px rgba(0, 0, 0, 0.76), 0 3px 10px rgba(0, 0, 0, 0.35);
  position: relative;
}

.tuning-dial span {
  position: absolute;
  width: 3px;
  height: 19px;
  left: 50%;
  top: 8px;
  transform: translateX(-50%) rotate(28deg);
  transform-origin: 50% 25px;
  border-radius: 999px;
  background: #d9fff1;
}

.radar-pane {
  padding: 5px;
}

.radar-pane .scope-label {
  margin-bottom: 5px;
}

.radar-canvas {
  height: 92px;
}

.action-list {
  margin: 0;
  padding-left: 22px;
}

.action-list li {
  margin: 7px 0;
  color: #f4d084;
  line-height: 1.35;
}

.empty-state,
.empty-compact {
  color: #7897b0;
  border: 1px dashed #244662;
  border-radius: 8px;
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
}

.ticker {
  border-top: 1px solid #18324a;
  height: 42px;
  overflow: hidden;
}

.ticker-track {
  display: flex;
  gap: 42px;
  padding: 10px 24px;
  white-space: nowrap;
  animation: ticker 32s linear infinite;
}

.ticker-track span {
  color: #a8c7de;
}

@keyframes ticker {
  from { transform: translateX(0); }
  to { transform: translateX(-50%); }
}

.event-enter-active {
  transition: all 0.32s ease;
}

.event-enter-from {
  opacity: 0;
  transform: translateY(-10px);
}

@media (max-width: 1100px) {
  .workspace {
    grid-template-columns: 1fr;
    overflow-y: auto;
  }

  .ops-panel {
    overflow: visible;
  }

  .analyzer-shell {
    grid-template-columns: 8px minmax(0, 1fr) 8px;
    padding: 8px;
    gap: 6px;
  }

  .analyzer-body,
  .screen-grid {
    grid-template-columns: 1fr;
  }

  .channel-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .analyzer-controls {
    display: grid;
    grid-template-columns: 74px minmax(0, 1fr) 72px;
    align-items: center;
  }

  .rx-indicator {
    height: auto;
  }

  .tuning-dial {
    width: 56px;
    height: 56px;
  }

  .radar-pane {
    grid-column: 1 / -1;
  }

  .radar-canvas {
    height: 170px;
  }
}
</style>
