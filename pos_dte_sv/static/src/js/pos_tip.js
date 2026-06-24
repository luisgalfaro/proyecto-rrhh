/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

console.log("DTE SV POS TIP: pos_tip.js cargado. Enfoque: Propina precisa (Precio de Venta) al hacer click en Pagar.");

patch(PosStore.prototype, {
    async pay() {
        try {
            const order = this.getOrder();
            
            if (order) {
                let tipProduct = null;
                if (this.models && this.models["product.template"]) {
                    const tmplModel = this.models["product.template"];
                    let allProducts = typeof tmplModel.getAll === "function" ? tmplModel.getAll() : (tmplModel.records || tmplModel || []);
                    tipProduct = allProducts.find(p => 
                        p.default_code === "PROPINA" ||
                        (p.display_name && p.display_name.toUpperCase() === "PROPINA") ||
                        (p.name && p.name.toUpperCase() === "PROPINA")
                    );
                }

                if (tipProduct) {
                    let baseAmount = 0;
                    const tipProductId = tipProduct.id;
                    
                    for (const line of order.lines) {
                        const prod = typeof line.getProduct === 'function' ? line.getProduct() : line.product;
                        const prodId = prod ? prod.id : null;
                        
                        if (prodId !== tipProductId) {
                            // Extraer el precio de venta real de la línea usando la nueva arquitectura de Odoo 19
                            let linePrice = 0;
                            if (line.prices) {
                                linePrice = line.prices.total_included_currency !== undefined ? line.prices.total_included_currency : line.prices.total_excluded_currency;
                            } else if (line.price_subtotal_incl !== undefined) {
                                linePrice = line.price_subtotal_incl;
                            } else {
                                const priceUnit = typeof line.get_unit_price === 'function' ? line.get_unit_price() : (line.price_unit || 0);
                                const qty = typeof line.get_quantity === 'function' ? line.get_quantity() : (line.qty || 1);
                                linePrice = priceUnit * qty;
                            }
                            baseAmount += linePrice;
                        }
                    }
                    
                    const expectedTip = Math.round(baseAmount * 0.10 * 100) / 100;
                    
                    if (expectedTip > 0) {
                        // Registrar en la orden explícitamente para el recibo XML de Odoo 19
                        order.tip_amount = expectedTip;
                        
                        // Buscar si ya hay una línea de propina
                        const existingTipLine = order.lines.find(line => {
                            const prod = typeof line.getProduct === 'function' ? line.getProduct() : line.product;
                            return prod && prod.id === tipProductId;
                        });

                        if (existingTipLine) {
                            console.log("DTE SV POS TIP: Actualizando propina existente a:", expectedTip);
                            if (typeof existingTipLine.setUnitPrice === 'function') existingTipLine.setUnitPrice(expectedTip);
                            else if (typeof existingTipLine.set_unit_price === 'function') existingTipLine.set_unit_price(expectedTip);
                            else existingTipLine.price_unit = expectedTip;
                        } else {
                            console.log("DTE SV POS TIP: Insertando propina nueva al 10%:", expectedTip);
                            if (typeof this.addLineToOrder === 'function') {
                                await this.addLineToOrder({ 
                                    product_tmpl_id: tipProduct,
                                    price_unit: expectedTip, 
                                    qty: 1 
                                }, order, { merge: false });
                            } else if (order.add_product) {
                                order.add_product(tipProduct, { price: expectedTip, quantity: 1, merge: false });
                            }
                        }
                    } else {
                        order.tip_amount = 0;
                    }
                } else {
                    console.warn("DTE SV POS TIP: Producto PROPINA no encontrado en el caché.");
                }
            }
        } catch (e) {
            console.error("DTE SV POS TIP: Error inyectando propina", e);
        }
        
        return super.pay(...arguments);
    }
});
