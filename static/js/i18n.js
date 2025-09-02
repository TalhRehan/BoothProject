// i18n.js - Language switching system
(function() {
  'use strict';

  const STORAGE_KEY = 'preferred_language';
  const DEFAULT_LANG = 'en';

  let currentLang = DEFAULT_LANG;
  let translations = {};
  let isRTL = false;

  // Language configuration
  const LANG_CONFIG = {
    'en': {
      dir: 'ltr',
      font: 'system-ui, -apple-system, sans-serif',
      name: 'English'
    },
    'ar': {
      dir: 'rtl',
      font: 'Tajawal, Cairo, "Noto Sans Arabic", Arial, sans-serif',
      name: 'العربية'
    }
  };

  // Get saved language preference
  function getSavedLanguage() {
    try {
      return localStorage.getItem(STORAGE_KEY) || DEFAULT_LANG;
    } catch {
      return DEFAULT_LANG;
    }
  }

  // Save language preference
  function saveLanguage(lang) {
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // Fallback to cookie if localStorage fails
      document.cookie = `${STORAGE_KEY}=${lang}; path=/; max-age=31536000`;
    }
  }

  // Load translation file
  async function loadTranslations(lang) {
    try {
      const response = await fetch(`/static/locales/${lang}/translation.json`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (error) {
      console.warn(`Failed to load ${lang} translations:`, error);
      // Fallback to English if available
      if (lang !== 'en') {
        try {
          const fallback = await fetch('/static/locales/en/translation.json');
          return await fallback.json();
        } catch {
          return {};
        }
      }
      return {};
    }
  }

  // Get nested translation value
  function getTranslation(key, fallback = key) {
    const keys = key.split('.');
    let value = translations;

    for (const k of keys) {
      if (value && typeof value === 'object' && k in value) {
        value = value[k];
      } else {
        return fallback;
      }
    }

    return typeof value === 'string' ? value : fallback;
  }

  // Update DOM with translations
  function updateDOM() {
    // Update all elements with data-i18n attribute
    document.querySelectorAll('[data-i18n]').forEach(element => {
      const key = element.getAttribute('data-i18n');
      const translation = getTranslation(key);

      // Update text content
      if (element.tagName === 'INPUT' && (element.type === 'submit' || element.type === 'button')) {
        element.value = translation;
      } else if (element.tagName === 'INPUT' && element.hasAttribute('placeholder')) {
        element.placeholder = translation;
      } else if (element.tagName === 'TEXTAREA' && element.hasAttribute('placeholder')) {
        element.placeholder = translation;
      } else {
        element.textContent = translation;
      }
    });

    // Update placeholder attributes separately
    document.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
      const key = element.getAttribute('data-i18n-placeholder');
      element.placeholder = getTranslation(key);
    });

    // Update title attributes
    document.querySelectorAll('[data-i18n-title]').forEach(element => {
      const key = element.getAttribute('data-i18n-title');
      element.title = getTranslation(key);
    });

    // Update aria-label attributes
    document.querySelectorAll('[data-i18n-aria]').forEach(element => {
      const key = element.getAttribute('data-i18n-aria');
      element.setAttribute('aria-label', getTranslation(key));
    });

    // Update SVG text elements
    document.querySelectorAll('svg text[data-i18n]').forEach(element => {
      const key = element.getAttribute('data-i18n');
      element.textContent = getTranslation(key);
    });

    // ✅ Added to ensure content is shown only after translations
    document.documentElement.classList.add('i18n-loaded');
    document.documentElement.classList.remove('rtl-loading');
  }

  // Apply language changes to HTML
  function applyLanguage(lang) {
    const config = LANG_CONFIG[lang] || LANG_CONFIG[DEFAULT_LANG];
    const html = document.documentElement;

    // Update HTML attributes
    html.setAttribute('lang', lang);

    // Update font family for Arabic
    if (lang === 'ar') {
      document.body.style.fontFamily = config.font;
    } else {
      document.body.style.fontFamily = '';
    }

    // Set RTL flag for CSS
    isRTL = config.dir === 'rtl';
    document.body.classList.toggle('rtl', isRTL);

    currentLang = lang;
  }

  // Switch language
  async function switchLanguage(lang) {
    if (lang === currentLang) return;

    try {
      translations = await loadTranslations(lang);
      applyLanguage(lang);
      updateDOM();
      saveLanguage(lang);

      // Update language selector if it exists
      const languageSelect = document.getElementById('languageSelect');
      if (languageSelect) {
        languageSelect.value = lang;
      }

      return true;
    } catch (error) {
      console.error('Language switch failed:', error);
      return false;
    }
  }

  // Initialize language system
  async function initializeLanguage() {
    const savedLang = getSavedLanguage();

    // Load translations for saved language
    translations = await loadTranslations(savedLang);
    applyLanguage(savedLang);

    // Set up language selector if it exists
    const languageSelect = document.getElementById('languageSelect');
    if (languageSelect) {
      languageSelect.value = savedLang;

      // Don't auto-apply on change, wait for Apply Settings button
      // The existing applySettings handler will call our switch function
    }

    // Update DOM with initial translations
    updateDOM();
  }

  // Integrate with existing settings modal
  function integrateWithSettings() {
    const applySettings = document.getElementById('applySettings');
    const languageSelect = document.getElementById('languageSelect');

    if (applySettings && languageSelect) {
      // Store original handler (clone) if needed later
      const originalHandlers = applySettings.cloneNode(true);

      // Add language switching to apply settings
      applySettings.addEventListener('click', async (e) => {
        const selectedLang = languageSelect.value;
        if (selectedLang !== currentLang) {
          const success = await switchLanguage(selectedLang);
          if (success && window.toast) {
            // Use current language for toast message
            const message = currentLang === 'ar' ? 'تم تحديث الإعدادات بنجاح!' : 'Settings updated successfully!';
            window.toast.success(message);
          }
        }
      });
    }
  }

  // Expose globally for external access
  window.i18n = {
    switchLanguage,
    getCurrentLanguage: () => currentLang,
    isRTL: () => isRTL,
    t: getTranslation
  };

  // Pre-load and apply language immediately
  (async function() {
    const savedLang = getSavedLanguage();

    // Ensure body exists before manipulating (handles very-early script loads)
    const ensureBody = () =>
      new Promise(resolve => {
        if (document.body) return resolve();
        document.addEventListener('DOMContentLoaded', () => resolve(), { once: true });
      });
    await ensureBody();

    // Apply language attributes immediately (before content loads)
    if (savedLang === 'ar') {
      document.documentElement.setAttribute('lang', 'ar');
      document.body.classList.add('rtl');
      document.body.style.fontFamily = 'Tajawal, Cairo, "Noto Sans Arabic", Arial, sans-serif';
    }

    // Hide body briefly to prevent flash
    document.body.style.visibility = 'hidden';

    // Load translations and initialize
    await initializeLanguage();

    // Show body after translations applied
    document.body.style.visibility = 'visible';

    integrateWithSettings();
  })();
})();
