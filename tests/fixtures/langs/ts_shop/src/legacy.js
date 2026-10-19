const { sum } = require("./core/money");
const tax = require("./billing/tax");

function total(values) {
  return tax.apply(sum(values));
}

module.exports = { total };
