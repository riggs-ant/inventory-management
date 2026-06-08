<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <!-- Budget card: always visible regardless of loading/error state -->
    <div class="card budget-card">
      <div class="budget-label">{{ t('restocking.budget') }}</div>
      <div class="budget-readout">{{ currencySymbol }}{{ budget.toLocaleString() }}</div>
      <input
        type="range"
        min="0"
        max="50000"
        step="500"
        v-model.number="budget"
        :style="{ background: sliderBackground }"
        class="budget-slider"
      />
      <div class="slider-range-labels">
        <span>{{ currencySymbol }}0</span>
        <span>{{ currencySymbol }}50,000</span>
      </div>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <!-- Summary stats -->
      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.itemsRecommended') }}</div>
          <div class="stat-value">{{ recommendations.length }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.totalCost') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ totalCost.toLocaleString() }}</div>
        </div>
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.remainingBudget') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ remainingBudget.toLocaleString() }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.skippedOverBudget') }}</div>
          <div class="stat-value">{{ skippedUnaffordable }}</div>
        </div>
      </div>

      <!-- Recommended items card -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }} ({{ recommendations.length }})</h3>
        </div>

        <!-- Empty states inside the card -->
        <div v-if="budget === 0" class="empty-state">
          {{ t('restocking.setBudget') }}
        </div>
        <div v-else-if="recommendations.length === 0" class="empty-state">
          {{ t('restocking.noRecommendations') }}
        </div>
        <div v-else class="table-container">
          <table class="restock-table">
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.itemName') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.onHand') }}</th>
                <th>{{ t('restocking.table.forecasted') }}</th>
                <th>{{ t('restocking.table.orderQty') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.sku">
                <td><strong>{{ rec.sku }}</strong></td>
                <td>{{ rec.name }}</td>
                <td>
                  <span :class="['badge', rec.trend]">{{ t('trends.' + rec.trend) }}</span>
                </td>
                <td>{{ rec.quantity_on_hand }}</td>
                <td>{{ rec.forecasted_demand }}</td>
                <!-- gap is the quantity to order; rendered bold to draw attention -->
                <td><strong>{{ rec.gap }}</strong></td>
                <td>{{ currencySymbol }}{{ rec.unit_cost.toLocaleString() }}</td>
                <td>{{ currencySymbol }}{{ rec.line_total.toLocaleString() }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Place Order action: disabled when nothing to order or POST is in flight -->
        <div class="order-actions">
          <button
            class="place-order-btn"
            :disabled="recommendations.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
          </button>
        </div>

        <!-- POST error feedback, shown inside the card near the button -->
        <div v-if="orderError" class="error order-error">{{ orderError }}</div>
      </div>

      <!-- Success banner: appears after a successful order placement -->
      <div v-if="lastOrder" class="order-success-banner">
        {{ t('restocking.orderPlaced', { orderNumber: lastOrder.order_number, date: formatDate(lastOrder.expected_delivery) }) }}
        {{ t('restocking.viewInOrders') }}
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale } = useI18n()

    const currencySymbol = computed(() => currentCurrency.value === 'JPY' ? '¥' : '$')

    const formatDate = (dateString) => {
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return new Date(dateString).toLocaleDateString(locale, {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    const budget = ref(10000)
    // Start in loading state to avoid a brief flash of empty content before mount
    const loading = ref(true)
    const error = ref(null)
    const recommendations = ref([])
    const totalCost = ref(0)
    const remainingBudget = ref(0)
    const skippedUnaffordable = ref(0)
    const submitting = ref(false)
    const lastOrder = ref(null)
    const orderError = ref(null)

    // Dynamically fill the range track left of the thumb with accent color
    const sliderBackground = computed(() => {
      const pct = (budget.value / 50000) * 100
      return `linear-gradient(to right, #3b82f6 ${pct}%, #e2e8f0 ${pct}%)`
    })

    const loadRecommendations = async () => {
      loading.value = true
      error.value = null
      try {
        const data = await api.getRestockingRecommendations(budget.value)
        recommendations.value = data.recommendations
        totalCost.value = data.total_cost
        remainingBudget.value = data.remaining_budget
        skippedUnaffordable.value = data.skipped_unaffordable
      } catch (err) {
        error.value = 'Failed to load recommendations: ' + err.message
      } finally {
        loading.value = false
      }
    }

    // Hold the debounce timer handle outside reactive state so it doesn't trigger watchers
    let debounceTimer = null

    watch(budget, () => {
      // Stale success banner no longer applies when the budget changes
      lastOrder.value = null
      orderError.value = null
      // Debounce: fire the API call only 250ms after the slider stops moving,
      // so rapid drag events don't flood the backend with requests
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(loadRecommendations, 250)
    })

    const placeOrder = async () => {
      submitting.value = true
      orderError.value = null
      try {
        const order = await api.createRestockOrder({
          items: recommendations.value.map(r => ({ sku: r.sku, quantity: r.gap }))
        })
        lastOrder.value = order
        // Mock data: stock levels don't decrement, so recommendations stay the same.
        // This is accepted demo behavior — the same items will remain recommended.
      } catch (err) {
        orderError.value = 'Failed to place order: ' + err.message
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadRecommendations)

    return {
      t,
      currencySymbol,
      formatDate,
      budget,
      sliderBackground,
      loading,
      error,
      recommendations,
      totalCost,
      remainingBudget,
      skippedUnaffordable,
      submitting,
      lastOrder,
      orderError,
      placeOrder
    }
  }
}
</script>

<style scoped>
/* Budget card layout */
.budget-card {
  margin-bottom: 1.5rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.5rem;
}

.budget-readout {
  font-size: 2.5rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
  margin-bottom: 1rem;
}

/* Cross-browser range slider reset */
.budget-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 3px;
  outline: none;
  cursor: pointer;
  margin-bottom: 0.5rem;
  /* background set via inline binding for the fill gradient */
}

/* WebKit thumb */
.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  background: #0f172a;
  border-radius: 50%;
  cursor: pointer;
  transition: background 0.15s ease, box-shadow 0.15s ease;
}

.budget-slider:hover::-webkit-slider-thumb {
  background: #3b82f6;
}

.budget-slider:focus::-webkit-slider-thumb {
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.35);
}

/* Firefox thumb */
.budget-slider::-moz-range-thumb {
  width: 20px;
  height: 20px;
  background: #0f172a;
  border-radius: 50%;
  cursor: pointer;
  border: none;
  transition: background 0.15s ease;
}

.budget-slider:hover::-moz-range-thumb {
  background: #3b82f6;
}

/* Min / max labels under the slider */
.slider-range-labels {
  display: flex;
  justify-content: space-between;
  font-size: 0.813rem;
  color: #94a3b8;
}

/* Muted, centered text for empty states inside the card */
.empty-state {
  text-align: center;
  padding: 2rem;
  color: #94a3b8;
  font-size: 0.938rem;
}

.restock-table {
  width: 100%;
}

/* Place Order button area */
.order-actions {
  padding: 1rem 0.75rem 0.5rem;
  border-top: 1px solid #f1f5f9;
  margin-top: 0.5rem;
}

.place-order-btn {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.75rem 2rem;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s ease, box-shadow 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  opacity: 0.9;
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.place-order-btn:disabled {
  background: #e2e8f0;
  color: #94a3b8;
  cursor: not-allowed;
}

.order-error {
  margin: 0.5rem 0.75rem 0.75rem;
}

/* Success banner mimics the .stat-card.success aesthetic */
.order-success-banner {
  background: #d1fae5;
  border: 1px solid #6ee7b7;
  color: #065f46;
  border-radius: 10px;
  padding: 1.25rem;
  margin-bottom: 1.25rem;
  font-weight: 500;
  font-size: 0.938rem;
}
</style>
