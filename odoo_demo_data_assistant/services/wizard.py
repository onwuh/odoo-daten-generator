"""
Wizard-Service für die Demo-Daten-Generierung.
Enthält die Hauptlogik aus connect.py, angepasst für die Modulstruktur.
"""
import os
import random
from typing import Dict, Any, List, Optional

from . import odoo_actions
from . import gemini_client


def populate_odoo_with_data(creative_data, criteria, client, gemini_model_name=None, language_name="German", tracking_selections=None):
    """Iterates through the creative data and creates the entries in Odoo."""
    if not creative_data:
        print("Keine Daten zum Verarbeiten vorhanden.")
        return {"product_ids": [], "company_ids": [], "order_ids": []}

    all_product_ids = []
    created_company_ids = []
    
    # Setup product enhancements
    industry = criteria.get('industry', 'IT')
    
    # Create product categories
    category_ids = odoo_actions.create_product_categories(client, industry, language_name)
    
    # Get existing UOMs
    existing_uoms = odoo_actions.get_existing_uoms(client)
    print(f"-> Found {len(existing_uoms)} existing UOMs")
    
    # Get existing barcodes for uniqueness
    existing_barcodes = odoo_actions.get_existing_barcodes(client)
    print(f"-> Found {len(existing_barcodes)} existing barcodes")
    
    # Get existing default_codes for uniqueness
    existing_default_codes = odoo_actions.get_existing_default_codes(client)
    print(f"-> Found {len(existing_default_codes)} existing internal references")

    # Tracking setup
    use_tracking = tracking_selections and tracking_selections.get("use_tracking", False)
    lot_enabled = tracking_selections and tracking_selections.get("lot_enabled", False)
    serial_enabled = tracking_selections and tracking_selections.get("serial_enabled", False)
    
    # Track which tracking types we've created (for ensuring we have exactly one of each)
    tracking_created = {"lot": False, "serial": False}
    tracking_products = []  # Store products with tracking that are not in BOMs
    
    # Counter for storable products to assign tracking deterministically
    storable_counter = 0

    print("\n--- Erstelle Produkte ---")
    product_map = {
        'services': {'type': 'service'},
        'consumables': {'type': 'consu', 'is_storable': False},
        'storables': {'type': 'consu', 'is_storable': True}
    }
    # Invalid fields to filter out from Gemini-generated data
    invalid_product_fields = {'uom', 'vat', 'vat_id', 'detailed_type'}
    
    for product_type, template in product_map.items():
        for creative_product in creative_data.get('products', {}).get(product_type, []):
            final_product_data = template.copy()
            valid_creative_data = {k: v for k, v in creative_product.items() 
                                 if v is not None and k not in invalid_product_fields}
            final_product_data.update(valid_creative_data)
            
            # Ensure is_storable is set correctly based on product type
            if product_type == 'storables':
                final_product_data['is_storable'] = True
            elif product_type == 'consumables':
                final_product_data['is_storable'] = False
            
            # Assign tracking for storable products: exactly one serial, exactly one lot
            if use_tracking and product_type == 'storables' and final_product_data.get('is_storable'):
                if storable_counter == 0 and serial_enabled and not tracking_created['serial']:
                    # First storable product gets serial tracking
                    final_product_data['tracking'] = 'serial'
                    tracking_created['serial'] = True
                    tracking_products.append(('serial', None))  # Will be updated with product_id
                elif storable_counter == 1 and lot_enabled and not tracking_created['lot']:
                    # Second storable product gets lot tracking
                    final_product_data['tracking'] = 'lot'
                    tracking_created['lot'] = True
                    tracking_products.append(('lot', None))  # Will be updated with product_id
                else:
                    # All other storable products get no tracking
                    final_product_data['tracking'] = 'none'
                storable_counter += 1
            else:
                final_product_data['tracking'] = 'none'
            
            # Ensure realistic price fields
            if 'list_price' not in final_product_data:
                final_product_data['list_price'] = round(random.uniform(15, 500), 2)
            if 'standard_price' not in final_product_data:
                final_product_data['standard_price'] = round(final_product_data['list_price'] * random.uniform(0.4, 0.8), 2)
            
            # Add product enhancements
            product_name = final_product_data.get('name', '')
            
            # 1. Barcode (unique)
            if 'barcode' not in final_product_data:
                barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
                if barcode:
                    final_product_data['barcode'] = barcode
            
            # 2. Internal reference (default_code) - unique
            if 'default_code' not in final_product_data:
                default_code = odoo_actions.generate_unique_default_code(client, product_name, existing_default_codes)
                if default_code:
                    final_product_data['default_code'] = default_code
            
            # 3. Weight (realistic based on product type)
            if 'weight' not in final_product_data:
                if product_type == 'services':
                    final_product_data['weight'] = 0.0
                elif product_type == 'consumables':
                    # Light consumables: 0.1 - 2 kg
                    final_product_data['weight'] = round(random.uniform(0.1, 2.0), 3)
                else:  # storables
                    # Heavier storable items: 0.5 - 50 kg
                    final_product_data['weight'] = round(random.uniform(0.5, 50.0), 3)
            
            # 4. Product category (assign logically)
            if 'categ_id' not in final_product_data and category_ids:
                final_product_data['categ_id'] = random.choice(category_ids)
            
            # 5. UOM assignment (using Gemini)
            if 'uom_id' not in final_product_data and existing_uoms:
                uom_id = gemini_client.fetch_uom_assignment(
                    product_name,
                    template.get('type', 'consu'),
                    industry,
                    existing_uoms,
                    gemini_model_name,
                    language_name
                )
                if uom_id:
                    final_product_data['uom_id'] = uom_id
                else:
                    # Fallback: use first available UOM
                    if existing_uoms:
                        first_uom_id = existing_uoms[0].get("id")
                        if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                            first_uom_id = first_uom_id[0]
                        final_product_data['uom_id'] = first_uom_id
            
            if 'name' in final_product_data:
                new_id = odoo_actions.create_product(client, final_product_data)
                all_product_ids.append(new_id)
                # Update tracking_products list with actual product ID
                if use_tracking and final_product_data.get('tracking') in ['serial', 'lot']:
                    for idx, (track_type, prod_id) in enumerate(tracking_products):
                        if prod_id is None and track_type == final_product_data.get('tracking'):
                            tracking_products[idx] = (track_type, new_id)
                            break

    # Ensure we have at least one product with serial and one with lot (not in BOMs)
    if use_tracking:
        # Create additional products if needed
        if not tracking_created.get('serial') and serial_enabled:
            print("-> Creating additional product with serial number tracking")
            serial_product_data = {
                "name": f"{industry} Serienprodukt",
                "type": "consu",
                "is_storable": True,
                "tracking": "serial",
                "list_price": round(random.uniform(50, 300), 2),
                "standard_price": round(random.uniform(30, 200), 2)
            }
            # Add enhancements
            barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
            if barcode:
                serial_product_data['barcode'] = barcode
            default_code = odoo_actions.generate_unique_default_code(client, serial_product_data['name'], existing_default_codes)
            if default_code:
                serial_product_data['default_code'] = default_code
            serial_product_data['weight'] = round(random.uniform(0.5, 10.0), 3)
            if category_ids:
                serial_product_data['categ_id'] = random.choice(category_ids)
            if existing_uoms:
                first_uom_id = existing_uoms[0].get("id")
                if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                    first_uom_id = first_uom_id[0]
                serial_product_data['uom_id'] = first_uom_id
            serial_id = odoo_actions.create_product(client, serial_product_data)
            all_product_ids.append(serial_id)
            tracking_products.append(('serial', serial_id))
        
        if not tracking_created.get('lot') and lot_enabled:
            print("-> Creating additional product with lot number tracking")
            lot_product_data = {
                "name": f"{industry} Losprodukt",
                "type": "consu",
                "is_storable": True,
                "tracking": "lot",
                "list_price": round(random.uniform(50, 300), 2),
                "standard_price": round(random.uniform(30, 200), 2)
            }
            # Add enhancements
            barcode = odoo_actions.generate_unique_barcode(client, existing_barcodes)
            if barcode:
                lot_product_data['barcode'] = barcode
            default_code = odoo_actions.generate_unique_default_code(client, lot_product_data['name'], existing_default_codes)
            if default_code:
                lot_product_data['default_code'] = default_code
            lot_product_data['weight'] = round(random.uniform(0.5, 10.0), 3)
            if category_ids:
                lot_product_data['categ_id'] = random.choice(category_ids)
            if existing_uoms:
                first_uom_id = existing_uoms[0].get("id")
                if isinstance(first_uom_id, (list, tuple)) and len(first_uom_id) > 0:
                    first_uom_id = first_uom_id[0]
                lot_product_data['uom_id'] = first_uom_id
            lot_id = odoo_actions.create_product(client, lot_product_data)
            all_product_ids.append(lot_id)
            tracking_products.append(('lot', lot_id))

    # --- 2. KUNDEN & KONTAKTE ERSTELLEN ---
    print("\n--- Erstelle Kunden und Kontakte ---")
    for company_scenario in creative_data.get('companies', []):
        company_data = company_scenario.get('company_data', {})
        if not company_data.get('name'): continue
        
        # Process main company address
        valid_company_data = {k: v for k, v in company_data.items() if v is not None}
        # Remove VAT ID to avoid validation errors
        valid_company_data.pop('vat', None)
        valid_company_data.pop('vat_id', None)
        country_code = valid_company_data.pop('country_code', 'DE')
        country_id = odoo_actions.get_country_id(client, country_code)
        if country_id:
            valid_company_data['country_id'] = country_id
        
        valid_company_data['company_type'] = 'company'
        company_id = odoo_actions.create_customer(client, valid_company_data)
        created_company_ids.append(company_id)
        
        # Process sub-contacts
        for contact_data in company_scenario.get('contacts', []):
            valid_contact_data = {k: v for k, v in contact_data.items() if v is not None}
            # Remove VAT ID to avoid validation errors
            valid_contact_data.pop('vat', None)
            valid_contact_data.pop('vat_id', None)
            valid_contact_data['parent_id'] = company_id
            
            # NEU: Verarbeite die individuelle Adresse des Sub-Kontakts, falls vorhanden
            if 'country_code' in valid_contact_data:
                contact_country_code = valid_contact_data.pop('country_code', 'DE')
                contact_country_id = odoo_actions.get_country_id(client, contact_country_code)
                if contact_country_id:
                    valid_contact_data['country_id'] = contact_country_id
            
            odoo_actions.create_customer(client, valid_contact_data)

    if "Bewegungsdaten" in criteria['mode'] and created_company_ids and all_product_ids:
        print("\n--- Erstelle Angebote (Bewegungsdaten) ---")
        for company_id in created_company_ids:
            products_for_order = all_product_ids[:2]
            if not products_for_order:
                continue

            order_lines_payload = []
            for product_id in products_for_order:
                order_lines_payload.append((0, 0, {'product_id': product_id, 'product_uom_qty': 1}))
            
            order_payload = {'partner_id': company_id, 'order_line': order_lines_payload}
            odoo_actions.create_sale_order(client, order_payload)

    return {
        "product_ids": all_product_ids,
        "company_ids": created_company_ids,
        "order_ids": [],
        "tracking_products": tracking_products  # Products with tracking that are not in BOMs
    }
