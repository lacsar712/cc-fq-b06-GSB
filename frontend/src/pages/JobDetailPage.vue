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

    <div class="row items-center q-mb-sm">
      <div class="text-subtitle1">Actor 阶段时间线</div>
      <q-space />
      <div class="text-caption text-grey-7">
        共 {{ totalStageCount }} 个阶段，当前显示 {{ stages.length }} 个
      </div>
    </div>

    <q-card flat bordered class="q-mb-md">
      <q-card-section class="q-pa-sm">
        <div class="row items-center q-col-gutter-sm">
          <div class="col-12 col-sm-5 col-md-4">
            <q-select
              v-model="filters.statuses"
              :options="statusOptions"
              multiple
              use-chips
              dense
              outlined
              emit-value
              map-options
              label="阶段状态（可多选）"
              clearable
            />
          </div>
          <div class="col-12 col-sm-4 col-md-4">
            <q-input
              v-model="filters.keyword"
              dense
              outlined
              clearable
              label="消息 / 阶段名关键字"
              @keyup.enter="applyFilters"
              @clear="applyFilters"
            />
          </div>
          <div class="col-auto">
            <q-btn color="primary" icon="filter_list" label="筛选" :loading="loading" @click="applyFilters" />
          </div>
          <div class="col-auto">
            <q-btn
              flat
              icon="restart_alt"
              label="重置"
              :disable="!hasActiveFilters"
              @click="resetFilters"
            />
          </div>
        </div>
        <div v-if="restorable" class="text-caption text-orange-8 q-mt-sm">
          已恢复上次的筛选条件，可点“重置”查看全部阶段
        </div>
      </q-card-section>
    </q-card>

    <q-timeline color="primary" class="q-mb-lg">
      <q-timeline-entry
        v-for="s in stages"
        :key="s.id"
        :subtitle="stageSubtitle(s)"
        :color="stageColor(s.status)"
        :icon="stageIcon(s.status)"
      >
        <template #title>
          <span class="text-caption text-grey-7 q-mr-xs">#{{ s.stage_order + 1 }}</span>
          <span>{{ s.actor_name }}</span>
        </template>
        <div>{{ s.message || '—' }}</div>
      </q-timeline-entry>
    </q-timeline>
    <q-banner v-if="!loading && !stages.length" rounded class="q-mb-lg bg-grey-2">
      没有符合筛选条件的阶段
    </q-banner>

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
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useQuasar } from 'quasar'
import { getJob, getJobStages } from '../api/client'

const route = useRoute()
const router = useRouter()
const $q = useQuasar()
const loading = ref(false)
const job = ref(null)
const stages = ref([])
let timer = null

const STORAGE_PREFIX = 'fq_stage_filters:'
const STATUS_VALUES = ['pending', 'running', 'success', 'failed', 'skipped']
const statusOptions = STATUS_VALUES.map((v) => ({
  value: v,
  label: stageStatusLabel(v),
}))

// 全量阶段数取自作业详情接口（job.stages 不参与时间线渲染，仅用于计数），
// 时间线只渲染阶段查询接口按条件返回的行，杜绝前端藏行。
const totalStageCount = computed(() => job.value?.stages?.length ?? 0)

const filters = reactive({ statuses: [], keyword: '' })
const restorable = ref(false)

const hasActiveFilters = computed(
  () => filters.statuses.length > 0 || filters.keyword.trim() !== '',
)

function storageKey() {
  return `${STORAGE_PREFIX}${route.params.id}`
}

function normalizeStatuses(list) {
  return (Array.isArray(list) ? list : []).filter((v) => STATUS_VALUES.includes(v))
}

// 优先级：URL query（可分享）> localStorage（个人持久化）> 空
function restoreFilters() {
  const qStatus = typeof route.query.status === 'string' ? route.query.status.split(',') : route.query.status
  const qKeyword = typeof route.query.keyword === 'string' ? route.query.keyword : ''

  let saved = null
  try {
    saved = JSON.parse(localStorage.getItem(storageKey()) || 'null')
  } catch {
    saved = null
  }

  if (qStatus || qKeyword) {
    filters.statuses = normalizeStatuses(qStatus || [])
    filters.keyword = qKeyword
    restorable.value = filters.statuses.length > 0 || qKeyword.trim() !== ''
  } else if (saved && (saved.statuses?.length || (saved.keyword || '').trim())) {
    filters.statuses = normalizeStatuses(saved.statuses)
    filters.keyword = saved.keyword || ''
    restorable.value = true
  }
}

function persistFilters() {
  localStorage.setItem(
    storageKey(),
    JSON.stringify({ statuses: filters.statuses, keyword: filters.keyword }),
  )
  const query = {}
  if (filters.statuses.length) query.status = filters.statuses.join(',')
  if (filters.keyword.trim()) query.keyword = filters.keyword.trim()
  router.replace({ query })
}

function buildParams() {
  const params = {}
  if (filters.statuses.length) params.status = filters.statuses.join(',')
  if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
  return params
}

async function applyFilters() {
  filters.statuses = normalizeStatuses(filters.statuses)
  filters.keyword = filters.keyword.trim()
  persistFilters()
  restorable.value = hasActiveFilters.value
  await loadStages()
}

async function resetFilters() {
  filters.statuses = []
  filters.keyword = ''
  persistFilters()
  restorable.value = false
  await loadStages()
}

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
  return stageStatusLabel(s)
}

function stageStatusLabel(s) {
  return (
    {
      pending: '排队中',
      running: '运行中',
      success: '成功',
      failed: '失败',
      skipped: '已跳过',
    }[s] || s
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
  stages.value = await getJobStages(route.params.id, buildParams())
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
  restoreFilters()
  if (hasActiveFilters.value && !route.query.status && !route.query.keyword) {
    // 从本地存储恢复时同步 URL，保证刷新 / 分享链接与当前筛选一致
    router.replace({ query: buildParams() })
  }
  await load()
  timer = setInterval(async () => {
    if (job.value && (job.value.status === 'pending' || job.value.status === 'running')) {
      // 轮询沿用当前筛选参数，运行中阶段的过滤视图保持一致
      loading.value = true
      try {
        job.value = await getJob(route.params.id)
        await loadStages()
      } catch (e) {
        $q.notify({ type: 'negative', message: e.message || '加载失败' })
      } finally {
        loading.value = false
      }
    }
  }, 1500)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

watch(
  () => route.params.id,
  async (newId, oldId) => {
    if (newId && newId !== oldId) {
      filters.statuses = []
      filters.keyword = ''
      restorable.value = false
      restoreFilters()
      await load()
    }
  },
)
</script>
