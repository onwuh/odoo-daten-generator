def create_customer(models, db_info, customer_data):
    """Creates a new customer and returns its ID."""
    print(f"-> Creating Customer/Contact: {customer_data.get('name')}...")
    db, uid, password = db_info
    customer_id = models.execute_kw(db, uid, password, 'res.partner', 'create', [customer_data])
    print(f"   ID: {customer_id}")
    return customer_id

def create_product(models, db_info, product_data):
    """Creates a new product and returns its ID."""
    print(f"-> Creating Product: {product_data.get('name')}...")
    db, uid, password = db_info
    product_id = models.execute_kw(db, uid, password, 'product.product', 'create', [product_data])
    print(f"   ID: {product_id}")
    return product_id

def create_sale_order(models, db_info, order_data):
    """Creates a new sale order and returns its ID."""
    print("-> Creating Sale Order...")
    db, uid, password = db_info
    order_id = models.execute_kw(db, uid, password, 'sale.order', 'create', [order_data])
    print(f"   ID: {order_id}")
    return order_id

def get_country_id(models, db_info, country_code):
    """Finds the Odoo database ID for a given country code (e.g., 'DE')."""
    db, uid, password = db_info
    # Search for the country with the matching code
    country_info = models.execute_kw(
        db, uid, password, 
        'res.country', 
        'search_read', 
        [[['code', '=', country_code.upper()]]], 
        {'fields': ['id'], 'limit': 1}
    )
    if country_info:
        return country_info[0]['id']
    return False # Return False if country not found