import { Component, ElementRef, input, output, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

/**
 * Confirmation for destructive actions. Uses the native <dialog> (focus trap + Escape for free).
 * With `typeToConfirm`, the action stays disabled until that exact text is typed (SPEC §9).
 */
@Component({
  selector: 'app-confirm-dialog',
  imports: [FormsModule],
  template: `
    <dialog
      #dlg
      class="m-auto w-full max-w-md rounded-lg border border-zinc-300 bg-white p-6 text-zinc-900 backdrop:bg-black/50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      [attr.aria-labelledby]="'dlg-title'"
      (close)="reset()"
    >
      <h2 id="dlg-title" class="text-lg font-semibold">{{ heading() }}</h2>
      <p class="mt-2 text-sm text-zinc-700 dark:text-zinc-300">{{ message() }}</p>
      @if (typeToConfirm()) {
        <label class="label mt-4" for="dlg-confirm">
          Type <strong>{{ typeToConfirm() }}</strong> to confirm
        </label>
        <input id="dlg-confirm" class="input" autocomplete="off" [(ngModel)]="typed" />
      }
      @if (error()) {
        <p class="alert-error mt-3" role="alert">{{ error() }}</p>
      }
      <div class="mt-5 flex justify-end gap-2">
        <button type="button" class="btn btn-secondary" (click)="close()">Cancel</button>
        <button
          type="button"
          class="btn btn-danger"
          [disabled]="busy() || (typeToConfirm() !== '' && typed() !== typeToConfirm())"
          (click)="confirmed.emit()"
        >
          {{ busy() ? 'Working…' : actionLabel() }}
        </button>
      </div>
    </dialog>
  `,
})
export class ConfirmDialogComponent {
  readonly heading = input.required<string>();
  readonly message = input.required<string>();
  readonly actionLabel = input('Delete');
  readonly typeToConfirm = input('');
  readonly busy = input(false);
  readonly error = input('');
  readonly confirmed = output<void>();

  protected readonly typed = signal('');
  private readonly dlg = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');

  open(): void {
    this.typed.set('');
    this.dlg().nativeElement.showModal();
  }

  close(): void {
    this.dlg().nativeElement.close();
  }

  protected reset(): void {
    this.typed.set('');
  }
}
