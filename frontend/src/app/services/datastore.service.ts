import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';
import { mainRoutes, adminRoutes } from '../website/config/config';
import {
  AppView,
  ContentView,
  RouteNode,
  UserInfo,
} from '../website/config/config.model';

@Injectable({
  providedIn: 'root',
})
export class DataStoreService {
  userInfo: UserInfo | null = null;

  mainRoutes: RouteNode[] = mainRoutes;

  private _view = new BehaviorSubject<AppView>('loading');

  private _currentContentView = new BehaviorSubject<ContentView>('main');
  private _currentPageTitle = new BehaviorSubject<string>(
    localStorage.getItem('pageTitle') || 'Chats',
  );

  get view$(): Observable<AppView> {
    return this._view.asObservable();
  }

  getview(): AppView {
    return this._view.getValue();
  }

  setview(value: AppView): void {
    this._view.next(value);
  }

  getRoutes(): RouteNode[] {
    return this.mainRoutes;
  }

  getUserInfo(): UserInfo | null {
    return this.userInfo;
  }

  setUserInfo(userInfo: UserInfo | null): void {
    this.userInfo = userInfo;
  }

  get currentContentView$(): Observable<ContentView> {
    return this._currentContentView.asObservable();
  }

  get currentPageTitle$(): Observable<string> {
    return this._currentPageTitle.asObservable();
  }

  getCurrentContentView(): ContentView {
    return this._currentContentView.getValue();
  }

  changecurrentContentView(): void {
    if (this._currentContentView.getValue() === 'main') {
      this.mainRoutes = adminRoutes;
      this._currentContentView.next('admin');
    } else {
      this.mainRoutes = mainRoutes;
      this._currentContentView.next('main');
    }
  }

  changePageTitle(value: string): void {
    this._currentPageTitle.next(value);
    // save the page title to local storage
    localStorage.setItem('pageTitle', value);
  }

  clear(): void {
    this.userInfo = null;
    this._view.next('logout');

    this._currentContentView.next('main');
    this.mainRoutes = mainRoutes;
  }
}
