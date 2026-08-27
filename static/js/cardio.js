// Живой расчёт средней скорости/темпа в форме кардио.
// Порог 14 км/ч продублирован из workouts/models.SPEED_THRESHOLD_KMH.
const SPEED_THRESHOLD_KMH = 14;

function cardioForm() {
  return {
    metric: "",
    metricLabel: "",

    init() {
      const recalc = () => this.recalc();
      this.$el.querySelectorAll("input").forEach((input) => {
        input.addEventListener("input", recalc);
      });
      recalc();
    },

    recalc() {
      const value = (name) => {
        const field = this.$el.querySelector(`[name="${name}"]`);
        return field ? parseFloat(field.value.replace(",", ".")) || 0 : 0;
      };
      const minutes = value("duration_hours") * 60 + value("duration_minutes");
      const distance = value("distance_km");
      if (!minutes || !distance) {
        this.metric = "";
        return;
      }
      const speed = (distance * 60) / minutes;
      if (speed >= SPEED_THRESHOLD_KMH) {
        this.metricLabel = "Средняя скорость";
        this.metric = `${speed.toFixed(1).replace(".", ",")} км/ч`;
      } else {
        const paceSeconds = Math.round((minutes * 60) / distance);
        const paceMinutes = Math.floor(paceSeconds / 60);
        const rest = String(paceSeconds % 60).padStart(2, "0");
        this.metricLabel = "Средний темп";
        this.metric = `${paceMinutes}:${rest} /км`;
      }
    },
  };
}
