/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./accounts/templates/**/*.html",
    "./cart/templates/**/*.html",
    "./catalog/templates/catalog/**/*.html",
    "./orders/templates/orders/payment/**/*.html",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
