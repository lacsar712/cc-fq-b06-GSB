<template>
  <q-page class="page-pad">
    <div class="row items-center q-mb-md">
      <div class="text-h5">作业详情 #{{ job?.id || '…' }}</div>
      <q-space />
      <q-btn flat icon="refresh" label="刷新" @click="load" :loading="loading" />
      <q-btn flat label="返回历史" to="/jobs" />
    </div>

    <q-banner v-if="job" rounded class="q-mb-md" :class="statusBannerClass">
      状态：{{ statusLabel(job.status) }}
      · 样例：{{ job.sample_name }}
      · 提交人：{{ job.created_by }}
      <div v-if="job.error_message" class="q-mt-sm">失败原因：{{ job.error_message }}</div>
    </q-banner>

    <div class="text-subtitle1 q-mb-sm">Actor 阶段时间线</div>

    <q-card flat bordered class="q-mb-md">
      <q-card-section class="row items-center q-col-gutter-md">
        <div class="col-12 col-sm-5">
          <q-select
            v-model="filterStatuses"
            :options="statusOptions"
            multiple
            dense
            outlined
            emit-value
            map-options
            use-chips
            label="阶段状态（多选，空为全部）"
          />
        </div>
        <div class="col-12 col-sm-4">
          <q-input
            v-model="filterKeyword"
            dense
            outlined
            clearable
            label="消息关键字"
            placeholder="按阶段消息过滤"
          />
        </div>
        <div class="col-auto">
          <q-btn flat icon="filter_alt_off" label="重置筛选" @click="resetFilters" />
        </div>
        <div class="col-12 text-caption text-grey-7">
          命中 {{ stages.length }} 个阶段 · 序号为完整流水线序号（过滤由服务端执行）
        </div>
      </q-card-section>
    </q-card>

    <q-timeline v-if="stages.length" color="primary" class="q-mb-lg">
      <q-timeline-entry
        v-for="s in stages"
        :key="s.id"
        :title="`#${s.stage_order} ${s.actor_name}`"
        :subtitle="stageSubtitle(s)"
        :color="stageColor(s.status)"
        :icon="stageIcon(s.status)"
      >
        <div>{{ s.message || '—' }}</div>
      </q-timeline-entry>
    </q-timeline>
    <div v-else class="text-grey-6 q-mb-lg">无匹配阶段（可调整状态多选或消息关键字）</div>

    <div class="text-subtitle1 q-mb-sm">质控指标</div>
    <div class="row q-col-gutter-md" v-if="metrics">
      <div class="col-12 col-sm-4" v-for="m in metricCards" :key="m.label">
        <q-card flat bordered class="metric-card">
          <q-card-section>
            <div class="text-caption text-grey-7">{{ m.label }}</div>
            <div class="text-h5">{{ m.value }}</div>
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12" v-if="perPosPreview.length">
        <q-card flat bordered>
          <q-card-section>
            <div class="text-subtitle2 q-mb-sm">per_position 摘要（前 8 位）</div>
            <q-markup-table flat dense>
              <thead>
                <tr>
                  <th class="text-left">位点</th>
                  <th class="text-left">平均质量</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in perPosPreview" :key="p.position">
                  <td>{{ p.position }}</td>
                  <td>{{ p.mean_quality }}</td>
                </tr>
              </tbody>
            </q-markup-table>
          </q-card-section>
        </q-card>
      </div>
    </div>
    <div v-else class="text-grey-6">尚无指标（作业未成功完成或仍在运行）</div>
  </q-page>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useQuasar } from 'quasar'
import { getJob, getJobStages } from '../api/client'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const $q = useQuasar()
const auth = useAuthStore()
const loading = ref(false)
const job = ref(null)
const stages = ref([])
let timer = null
let filterDebounce = null

const STATUS_VALUES = ['pending', 'running', 'success', 'failed', 'skipped']
const statusOptions = [
  { label: '排队中', value: 'pending' },
  { label: '运行中', value: 'running' },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
  { label: '已跳过', value: 'skipped' },
]

// 个人筛选条件：按用户名持久化到 localStorage，刷新后自动恢复
const filterStorageKey = `fq_stage_filter:${auth.username || 'anon'}`

function readSavedFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem(filterStorageKey) || 'null')
    return {
      statuses: Array.isArray(saved?.statuses)
        ? saved.statuses.filter((s) => STATUS_VALUES.includes(s))
        : [],
      q: typeof saved?.q === 'string' ? saved.q : '',
    }
  } catch {
    return { statuses: [], q: '' }
  }
}

const savedFilters = readSavedFilters()
const filterStatuses = ref(savedFilters.statuses)
const filterKeyword = ref(savedFilters.q)

function persistFilters() {
  localStorage.setItem(
    filterStorageKey,
    JSON.stringify({ statuses: filterStatuses.value, q: filterKeyword.value || '' }),
  )
}

function resetFilters() {
  filterStatuses.value = []
  filterKeyword.value = ''
}

// 过滤条件变化 → 重新请求阶段接口（带查询参数，服务端过滤，不做前端藏行）
watch([filterStatuses, filterKeyword], () => {
  persistFilters()
  if (filterDebounce) clearTimeout(filterDebounce)
  filterDebounce = setTimeout(loadStages, 300)
})

const metrics = computed(() => job.value?.metrics || null)

const metricCards = computed(() => {
  const m = metrics.value
  if (!m) return []
  return [
    { label: 'reads', value: m.reads ?? m.summary?.reads ?? '—' },
    { label: 'mean_quality', value: m.mean_quality ?? m.summary?.mean_quality ?? '—' },
    { label: 'n_rate', value: m.n_rate ?? m.summary?.n_rate ?? '—' },
  ]
})

const perPosPreview = computed(() => {
  const list = metrics.value?.per_position || []
  return list.slice(0, 8)
})

const statusBannerClass = computed(() => {
  const s = job.value?.status
  if (s === 'success') return 'bg-positive text-white'
  if (s === 'failed') return 'bg-negative text-white'
  if (s === 'running') return 'bg-info text-dark'
  return 'bg-grey-3'
})

function statusLabel(s) {
  return (
    { pending: '排队中', running: '运行中', success: '成功', failed: '失败', skipped: '已跳过' }[
      s
    ] || s
  )
}

function stageColor(status) {
  return (
    {
      pending: 'grey',
      running: 'info',
      success: 'positive',
      failed: 'negative',
      skipped: 'warning',
    }[status] || 'grey'
  )
}

function stageIcon(status) {
  return (
    {
      pending: 'hourglass_empty',
      running: 'play_circle',
      success: 'check_circle',
      failed: 'error',
      skipped: 'skip_next',
    }[status] || 'circle'
  )
}

function stageSubtitle(s) {
  const parts = [statusLabel(s.status) || s.status]
  if (s.started_at) parts.push(`开始 ${formatTime(s.started_at)}`)
  if (s.finished_at) parts.push(`结束 ${formatTime(s.finished_at)}`)
  return parts.join(' · ')
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

async function loadStages() {
  const id = route.params.id
  stages.value = await getJobStages(id, {
    statuses: filterStatuses.value,
    q: filterKeyword.value || '',
  })
}

async function load() {
  loading.value = true
  try {
    const id = route.params.id
    job.value = await getJob(id)
    await loadStages()
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '加载失败' })
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  // 运行映射保持：作业未结束时轮询，阶段请求始终携带当前过滤参数
  timer = setInterval(async () => {
    if (job.value && (job.value.status === 'pending' || job.value.status === 'running')) {
      await load()
    }
  }, 1500)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (filterDebounce) clearTimeout(filterDebounce)
})
</script>
