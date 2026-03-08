import { defineConfig, presetUno, presetIcons } from 'unocss'

export default defineConfig({
  presets: [
    presetUno(),
    presetIcons({
      scale: 1.2,
      collections: {
        heroicons: () => import('@iconify-json/heroicons/icons.json', { with: { type: 'json' } }).then(i => i.default),
      }
    }),
  ],
  theme: {
    colors: {
      primary: { DEFAULT: '#4F46E5', dark: '#4338CA', light: '#EEF2FF' }, // Indigo-based more premium
      success: { DEFAULT: '#10B981', light: '#D1FAE5', dark: '#059669' },
      warning: { DEFAULT: '#F59E0B', light: '#FEF3C7', dark: '#D97706' },
      danger: { DEFAULT: '#EF4444', light: '#FEE2E2', dark: '#DC2626' },
      neutral: { DEFAULT: '#6B7280', light: '#F9FAFB' },
      surface: { DEFAULT: '#FFFFFF', dark: '#0F172A' },
      background: { DEFAULT: '#F8FAFC', dark: '#020617' }
    },
    fontFamily: {
      sans: ['Inter', 'system-ui', 'sans-serif'],
    },
    boxShadow: {
      'glass': '0 4px 30px rgba(0, 0, 0, 0.05)',
      'card': '0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -2px rgba(0, 0, 0, 0.025)',
      'premium': '0 20px 25px -5px rgba(0, 0, 0, 0.05), 0 10px 10px -5px rgba(0, 0, 0, 0.02)',
      'nav': '0 -5px 15px rgba(0,0,0,0.05)'
    }
  },
  shortcuts: {
    'btn': 'px-4 py-2.5 rounded-xl font-medium transition-all duration-200 flex items-center justify-center transform active:scale-95',
    'btn-primary': 'btn bg-primary text-white shadow-md hover:shadow-lg hover:bg-primary-dark',
    'btn-outline': 'btn border-2 border-primary text-primary hover:bg-primary-light',
    'card': 'bg-surface rounded-2xl shadow-card border border-gray-100 p-6 transition-all duration-200 hover:shadow-premium',
    'card-compact': 'bg-surface rounded-xl shadow-card border border-gray-50 p-4 transition-all duration-200 active:scale-[0.98]',
    'badge': 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold uppercase tracking-wider',
    'badge-green': 'badge bg-success-light text-success-dark',
    'badge-yellow': 'badge bg-warning-light text-warning-dark',
    'badge-red': 'badge bg-danger-light text-danger-dark',
    'input': 'w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-all duration-200 focus:bg-white',
    'nav-item': 'flex flex-col items-center justify-center w-full h-full text-gray-500 hover:text-primary transition-colors duration-200',
    'nav-item-active': 'flex flex-col items-center justify-center w-full h-full text-primary font-medium',
  }
})
