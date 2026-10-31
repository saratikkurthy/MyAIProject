from sklearn.tree import DecisionTreeClassifier
from sklearn.cluster import KMeans

# ---------- Part A: Supervised learning ----------
# Features: [claim_amount, provider_age_in_months]
# Label: 0 = not fraud, 1 = fraud
X_train = [
    [500, 60], [800, 48], [1200, 36],   # normal claims
    [9500, 2], [12000, 1], [8000, 3],   # fraud-like claims
]
y_train = [0, 0, 0, 1, 1, 1]

model = DecisionTreeClassifier()
model.fit(X_train, y_train)   # <-- TRAINING: learning from labelled examples

# INFERENCE: predict a brand-new claim the model has never seen
print("Supervised prediction for [9000, 2]:", model.predict([[9000, 2]]))
print("Supervised prediction for [700, 50]:", model.predict([[700, 50]]))

# ---------- Part B: Unsupervised learning ----------
# Features: [age, monthly_spend] - NO labels at all
customers = [
    [25, 200], [28, 250], [24, 180],
    [45, 2000], [50, 2200], [48, 1900],
    [35, 800], [38, 900], [33, 750],
]

kmeans = KMeans(n_clusters=3, n_init=10, random_state=42)
clusters = kmeans.fit_predict(customers)  # <-- learns groups, no labels given

print("Unsupervised clusters:", clusters)
# Customers in the same cluster number belong to the same group
